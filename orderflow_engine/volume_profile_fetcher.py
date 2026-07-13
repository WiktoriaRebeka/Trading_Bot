# orderflow_engine/volume_profile_fetcher.py
# Async fetch VP z Bybit przy OB_NEW — bez blokowania emisji sygnału.

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

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

VP_API_CONCURRENCY = int(os.environ.get("VP_API_CONCURRENCY", "6"))
VP_FETCH_CACHE_TTL_SEC = float(os.environ.get("VP_FETCH_CACHE_TTL_SEC", "30"))
VP_API_RETRIES = int(os.environ.get("VP_API_RETRIES", "3"))
VP_API_BACKOFF_SEC = [1.0, 2.0, 5.0]


class VolumeProfileFetcher:
    """Pobiera świece 1M z API i liczy dual VP (5h + 24h z jednego fetcha)."""

    def __init__(self, backfiller: Optional[HistoryBackfiller] = None):
        self._backfiller = backfiller or HistoryBackfiller()
        self._owns_backfiller = backfiller is None
        self._session_open = False
        self._semaphore = asyncio.Semaphore(VP_API_CONCURRENCY)
        self._cache: Dict[Tuple[str, int], Tuple[float, list]] = {}
        self._cache_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._session_open:
            await self._backfiller.__aenter__()
            self._session_open = True

    async def close(self) -> None:
        if self._session_open and self._owns_backfiller:
            await self._backfiller.__aexit__(None, None, None)
        self._session_open = False
        self._cache.clear()

    async def fetch_dual_vp(
        self,
        symbol: str,
        detected_at_ts: int,
        ob: OrderBlock,
    ) -> Dict[str, Any]:
        sym = str(symbol).upper().replace(".P", "")
        setup = build_ob_trade_setup(ob, symbol=sym, log_prefix="[VP]")
        if setup is None:
            return empty_dual_vp_context()

        candles = await self._fetch_candles(sym, int(detected_at_ts))
        if not candles:
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
        cache_key = (symbol, end_ms // 60_000)
        now = time.time()
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                expires, rows = cached
                if now < expires:
                    return list(rows)

        rows: list = []
        for attempt in range(VP_API_RETRIES):
            async with self._semaphore:
                if not self._session_open:
                    await self.start()
                try:
                    result = await self._backfiller.fetch_klines_1m_paginated(
                        symbol,
                        end_ms=end_ms,
                        total_candles=VP_PERIOD_LONG,
                    )
                    rows = list(result.rows)
                    if result.ok and rows:
                        break
                    if rows and attempt + 1 >= VP_API_RETRIES:
                        break
                except Exception as e:
                    logger.warning(
                        "[VP-FETCH] %s próba %s/%s: %s: %s",
                        symbol, attempt + 1, VP_API_RETRIES, type(e).__name__, e,
                    )
            if attempt + 1 < VP_API_RETRIES:
                backoff = VP_API_BACKOFF_SEC[min(attempt, len(VP_API_BACKOFF_SEC) - 1)]
                await asyncio.sleep(backoff)

        if rows:
            async with self._cache_lock:
                self._cache[cache_key] = (now + VP_FETCH_CACHE_TTL_SEC, list(rows))
        return rows
