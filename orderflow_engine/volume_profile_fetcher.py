# orderflow_engine/volume_profile_fetcher.py
# Async fetch VP z Bybit przy OB_NEW — bez blokowania emisji sygnału.

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from orderflow_engine.backfiller import HistoryBackfiller
from orderflow_engine.msi_engine import OrderBlock
from orderflow_engine.ob_orderflow_snapshot import (
    VP_PERIOD_LONG,
    build_dual_vp_context,
    empty_dual_vp_context,
    normalize_vol_candle,
)
from shared_lib.ob_execution import build_ob_trade_setup

logger = logging.getLogger(__name__)

VP_API_CONCURRENCY = int(os.environ.get("VP_API_CONCURRENCY", "2"))
VP_FETCH_CACHE_TTL_SEC = float(os.environ.get("VP_FETCH_CACHE_TTL_SEC", "30"))
VP_API_RETRIES = int(os.environ.get("VP_API_RETRIES", "6"))
# Bybit public kline ~120 req/min — domyślnie 1.0 rps (~60/min, bezpieczny margines).
VP_API_MAX_RPS = float(os.environ.get("VP_API_MAX_RPS", "1.0"))


def _parse_backoff_sec() -> List[float]:
    raw = os.environ.get("VP_API_BACKOFF_SEC", "1,2,4,8,16,16").strip()
    try:
        vals = [float(x.strip()) for x in raw.split(",") if x.strip()]
        if vals:
            return vals
    except ValueError:
        pass
    return [1.0, 2.0, 4.0, 8.0, 16.0, 16.0]


VP_API_BACKOFF_SEC = _parse_backoff_sec()


class AsyncRateLimiter:
    """Globalny odstęp między zapytaniami VP → Bybit kline (min interval = 1/max_rps)."""

    def __init__(self, max_rps: float):
        self._min_interval = (1.0 / max_rps) if max_rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_ok_at = 0.0

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_ok_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_ok_at = now + self._min_interval


class VolumeProfileFetcher:
    """Pobiera świece 1M z API i liczy dual VP (5h + 24h z jednego fetcha)."""

    def __init__(self, backfiller: Optional[HistoryBackfiller] = None):
        self._backfiller = backfiller or HistoryBackfiller()
        self._owns_backfiller = backfiller is None
        self._session_open = False
        self._semaphore = asyncio.Semaphore(VP_API_CONCURRENCY)
        self._rate_limiter = AsyncRateLimiter(VP_API_MAX_RPS)
        self._cache: Dict[Tuple[str, int], Tuple[float, list]] = {}
        self._cache_lock = asyncio.Lock()
        # Domyślnie otwarta — testy / brak bootstrapa nie wiszą.
        # main.begin_bootstrap_hold() zamyka na czas MSI bootstrap.
        self._bootstrap_gate = asyncio.Event()
        self._bootstrap_gate.set()

    def begin_bootstrap_hold(self) -> None:
        """Wstrzymaj VP fetch na czas MSI bootstrap (oba biją w /v5/market/kline)."""
        self._bootstrap_gate.clear()
        logger.info("[VP-FETCH] bootstrap hold ON — VP czeka aż MSI bootstrap skończy")

    def mark_bootstrap_complete(self) -> None:
        self._bootstrap_gate.set()
        logger.info("[VP-FETCH] bootstrap hold OFF — VP fetch odblokowany")

    async def start(self) -> None:
        if not self._session_open:
            await self._backfiller.__aenter__()
            self._session_open = True

    async def close(self) -> None:
        if self._session_open and self._owns_backfiller:
            await self._backfiller.__aexit__(None, None, None)
        self._session_open = False
        self._cache.clear()
        # Odblokuj ewentualne wiszące wait — shutdown.
        self._bootstrap_gate.set()

    async def fetch_dual_vp(
        self,
        symbol: str,
        detected_at_ts: int,
        ob: OrderBlock,
    ) -> Dict[str, Any]:
        sym = str(symbol).upper().replace(".P", "")
        setup = build_ob_trade_setup(ob, symbol=sym, log_prefix="[VP]")
        if setup is None:
            logger.warning(
                "[VP-FETCH] %s empty VP — brak setup (filtr/sanity) chain=%s",
                sym,
                getattr(ob, "chain_id", None),
            )
            return empty_dual_vp_context()

        candles = await self._fetch_candles(sym, int(detected_at_ts))
        if not candles:
            logger.warning(
                "[VP-FETCH] %s empty VP — zero świec po fetch (end_ms=%s chain=%s)",
                sym,
                detected_at_ts,
                getattr(ob, "chain_id", None),
            )
            return empty_dual_vp_context()

        normalized = [normalize_vol_candle(c) for c in candles]
        return build_dual_vp_context(
            normalized,
            ob_high=setup.ob_high,
            ob_low=setup.ob_low,
            entry=setup.entry_limit,
            sl=setup.sl,
        )

    async def _fetch_candles(self, symbol: str, end_ms: int) -> list:
        await self._bootstrap_gate.wait()

        cache_key = (symbol, end_ms // 60_000)
        now = time.time()
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                expires, cached_rows = cached
                if now < expires:
                    return list(cached_rows)

        rows: list = []
        hit_rate_limit = False
        last_ok = False
        for attempt in range(VP_API_RETRIES):
            async with self._semaphore:
                await self._bootstrap_gate.wait()
                if not self._session_open:
                    await self.start()
                try:
                    result = await self._backfiller.fetch_klines_1m_paginated(
                        symbol,
                        end_ms=end_ms,
                        total_candles=VP_PERIOD_LONG,
                        rate_limiter=self._rate_limiter,
                    )
                    n_got = len(result.rows)
                    last_ok = bool(result.ok)
                    # Pełne okno albo nic — partial (nawet 340/1440) nigdy nie idzie do profilu.
                    if result.rate_limited:
                        hit_rate_limit = True
                        logger.warning(
                            "[VP-FETCH] %s rate limit — retry %s/%s (backoff) "
                            "discarded_partial_n=%s",
                            symbol, attempt + 1, VP_API_RETRIES, n_got,
                        )
                        rows = []
                        last_ok = False
                    elif result.ok and n_got == VP_PERIOD_LONG:
                        rows = list(result.rows)
                        break
                    else:
                        rows = []
                        last_ok = False
                        logger.warning(
                            "[VP-FETCH] %s incomplete/empty — discard partial "
                            "próba %s/%s ok=%s n=%s need=%s",
                            symbol, attempt + 1, VP_API_RETRIES, result.ok,
                            n_got, VP_PERIOD_LONG,
                        )
                except Exception as e:
                    logger.warning(
                        "[VP-FETCH] %s próba %s/%s: %s: %s",
                        symbol, attempt + 1, VP_API_RETRIES, type(e).__name__, e,
                    )
                    rows = []
                    last_ok = False
            if attempt + 1 < VP_API_RETRIES and not (last_ok and rows):
                backoff = VP_API_BACKOFF_SEC[min(attempt, len(VP_API_BACKOFF_SEC) - 1)]
                await asyncio.sleep(backoff)

        if rows:
            async with self._cache_lock:
                self._cache[cache_key] = (time.time() + VP_FETCH_CACHE_TTL_SEC, list(rows))
        elif not last_ok:
            logger.warning(
                "[VP-FETCH] %s returning empty after %s attempts "
                "end_ms=%s hit_rate_limit=%s",
                symbol, VP_API_RETRIES, end_ms, hit_rate_limit,
            )
        return rows
