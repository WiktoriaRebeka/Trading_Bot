# orderflow_engine/backfiller.py
import os
import aiohttp
import logging
from typing import List, NamedTuple, Any, Optional

from shared_lib import constants
from shared_lib.firebase_client import get_db

logger = logging.getLogger(__name__)


class FetchHistoryResult(NamedTuple):
    """Jawny status backfillu: rows + ok (True tylko przy sukcesie z niepustymi świecami)."""
    rows: List[dict[str, Any]]
    ok: bool
    rate_limited: bool = False


# Bybit: HTTP 429 lub retCode 10006 ("Too many visits") → retry u callera, nie „brak danych”.
BYBIT_RATE_LIMIT_RETCODES = frozenset({10006})


class HistoryBackfiller:
    def __init__(self, base_url="https://api.bybit.com"):
        self.base_url = base_url
        self.endpoint = f"{base_url}/v5/market/kline"
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        total = float(os.environ.get("BACKFILL_HTTP_TIMEOUT_SEC", "90"))
        total = max(30.0, total)
        timeout = aiohttp.ClientTimeout(total=total)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.close()
            self._session = None
        return False

    def fetch_from_firestore(self, symbol: str) -> FetchHistoryResult:
        """
        Pobiera klines z Firestore: kolekcja LATEST_KLINES_COLLECTION (shared_lib.constants),
        dokument = symbol (np. BTCUSDT).

        Odczyt zgodny z collector_service.data_collector.save_klines_to_firestore:

        - Pola na dokumencie ``symbol``: symbol, high, low, close, timestamp (ms) — jak w batch.set(..., merge=True).
        - Kolektor zapisuje jedną świecę; brak open — przy mapowaniu na wiersze pre_load open=close.
        - Opcjonalne pole ``m1``: lista słowników, każdy z polami high, low, close, timestamp (ten sam kształt świecy
          co w zapisie, bez symbolu na elemencie). Merge nie usuwa ``m1`` — umożliwia >=100 świec M1 w jednym dokumencie.
        """
        sym_api = str(symbol).replace(".P", "").upper()
        try:
            db = get_db()
            ref = db.collection(constants.LATEST_KLINES_COLLECTION).document(sym_api)
            snap = ref.get()
        except Exception as e:
            logger.warning(
                "⚠️ fetch_from_firestore %s: %s: %s",
                sym_api,
                type(e).__name__,
                e,
                exc_info=True,
            )
            return FetchHistoryResult([], False)

        if not snap.exists:
            return FetchHistoryResult([], False)

        d = snap.to_dict() or {}

        def _row_from_collector_candle(c: dict) -> Optional[dict[str, Any]]:
            if not all(k in c for k in ("high", "low", "close", "timestamp")):
                return None
            try:
                ts = int(c["timestamp"])
                close = float(c["close"])
                return {
                    "ts": ts,
                    "open": close,
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": close,
                    "volume": float(c.get("volume", 0.0)),
                }
            except (TypeError, ValueError, KeyError):
                return None

        rows: List[dict[str, Any]] = []
        m1_raw = d.get("m1")
        if isinstance(m1_raw, list) and m1_raw:
            for item in m1_raw:
                if isinstance(item, dict):
                    row = _row_from_collector_candle(item)
                    if row:
                        rows.append(row)
            rows.sort(key=lambda r: r["ts"])
        else:
            row = _row_from_collector_candle(d)
            if row:
                rows.append(row)

        if not rows:
            return FetchHistoryResult([], False)
        return FetchHistoryResult(rows, True)

    async def fetch_history(self, symbol, interval='1', limit=1000) -> FetchHistoryResult:
        """Pobiera klines; ok=True wyłącznie gdy otrzymano niepustą listę świec."""
        if self._session is None:
            raise RuntimeError("HistoryBackfiller wymaga async with przed fetch_history")

        params = {
            "category": "linear",
            "symbol": symbol.replace('.P', ''),
            "interval": interval,
            "limit": limit
        }
        try:
            async with self._session.get(self.endpoint, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("retCode") == 0 and data.get("result"):
                        klines = data["result"]["list"][::-1]
                        if not klines:
                            logger.warning(f"⚠️ Backfill {symbol}: retCode=0 ale pusta lista klines")
                            return FetchHistoryResult([], False)
                        rows = [{
                            'ts': int(k[0]),
                            'open': float(k[1]),
                            'high': float(k[2]),
                            'low': float(k[3]),
                            'close': float(k[4]),
                            'volume': float(k[5])
                        } for k in klines]
                        return FetchHistoryResult(rows, True)
                    else:
                        logger.error(
                            f"❌ Backfill {symbol}: retCode={data.get('retCode')} "
                            f"retMsg={data.get('retMsg')} result={bool(data.get('result'))}"
                        )
                else:
                    logger.error(f"❌ Bybit API Error {symbol}: HTTP {response.status}")
        except TimeoutError:
            logger.warning(
                "⚠️ Backfill %s interval=%s: HTTP timeout (limit całkowity aiohttp) — kolejna próba może pomóc",
                symbol,
                interval,
            )
        except aiohttp.ClientError as e:
            logger.warning(
                "⚠️ Backfill %s interval=%s: błąd klienta HTTP %s: %s",
                symbol,
                interval,
                type(e).__name__,
                e,
            )
        except Exception as e:
            logger.error(f"❌ Błąd backfillu dla {symbol}: {type(e).__name__}: {e}", exc_info=True)
        return FetchHistoryResult([], False)

    def _partial_vp_rows(
        self,
        by_ts: dict[int, dict[str, Any]],
        *,
        end_ms: int,
        total_candles: int,
    ) -> List[dict[str, Any]]:
        rows = sorted(by_ts.values(), key=lambda r: r["ts"])
        return [r for r in rows if r["ts"] <= end_ms][-total_candles:]

    async def fetch_klines_1m_paginated(
        self,
        symbol: str,
        *,
        end_ms: int,
        total_candles: int,
        rate_limiter: Any = None,
    ) -> FetchHistoryResult:
        """
        Świece 1M z volume, kończące się na end_ms (detected_at_ts).
        Paginacja gdy total_candles > 1000 (limit Bybit).
        Zwraca rows posortowane rosnąco; ok=True gdy len(rows) >= total_candles.
        rate_limited=True przy HTTP 429 lub retCode 10006 — caller ma retry + backoff.
        """
        if self._session is None:
            raise RuntimeError("HistoryBackfiller wymaga async with przed fetch_klines_1m_paginated")

        sym_api = str(symbol).replace(".P", "").upper()
        if total_candles <= 0:
            return FetchHistoryResult([], False)

        by_ts: dict[int, dict[str, Any]] = {}
        cursor_end = int(end_ms)
        start_target = cursor_end - (total_candles - 1) * 60_000
        max_pages = (total_candles + 999) // 1000 + 2

        for _ in range(max_pages):
            if len(by_ts) >= total_candles:
                break
            remaining = total_candles - len(by_ts)
            limit = min(1000, max(remaining, 200))
            params = {
                "category": "linear",
                "symbol": sym_api,
                "interval": "1",
                "end": cursor_end,
                "limit": limit,
            }
            if rate_limiter is not None:
                await rate_limiter.acquire()
            try:
                async with self._session.get(self.endpoint, params=params) as response:
                    if response.status == 429:
                        n_partial = len(by_ts)
                        logger.warning(
                            "[VP-FETCH] %s rate limit HTTP 429 end=%s discarded_partial_n=%s",
                            sym_api, cursor_end, n_partial,
                        )
                        # Partial tylko do logu — caller dostaje puste rows + rate_limited.
                        return FetchHistoryResult([], False, True)
                    if response.status != 200:
                        logger.error(
                            "[VP-FETCH] %s HTTP %s end=%s",
                            sym_api, response.status, cursor_end,
                        )
                        break
                    data = await response.json()
            except (TimeoutError, aiohttp.ClientError) as e:
                logger.warning(
                    "[VP-FETCH] %s błąd HTTP end=%s: %s: %s",
                    sym_api, cursor_end, type(e).__name__, e,
                )
                break

            ret_code = data.get("retCode")
            if ret_code in BYBIT_RATE_LIMIT_RETCODES:
                n_partial = len(by_ts)
                logger.warning(
                    "[VP-FETCH] %s rate limit retCode=%s retMsg=%s end=%s "
                    "discarded_partial_n=%s",
                    sym_api, ret_code, data.get("retMsg"), cursor_end, n_partial,
                )
                # Partial tylko do logu — caller dostaje puste rows + rate_limited.
                return FetchHistoryResult([], False, True)
            if ret_code != 0 or not data.get("result"):
                logger.warning(
                    "[VP-FETCH] %s retCode=%s retMsg=%s",
                    sym_api, ret_code, data.get("retMsg"),
                )
                break

            raw_list = data["result"]["list"] or []
            if not raw_list:
                break

            oldest_ts = None
            for k in raw_list:
                ts = int(k[0])
                if ts > cursor_end:
                    continue
                oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
                by_ts[ts] = {
                    "ts": ts,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }

            if oldest_ts is None:
                break
            if len(raw_list) < limit:
                break
            if oldest_ts <= start_target:
                break
            cursor_end = oldest_ts - 60_000

        rows = self._partial_vp_rows(by_ts, end_ms=end_ms, total_candles=total_candles)
        ok = len(rows) >= total_candles
        return FetchHistoryResult(rows, ok)
