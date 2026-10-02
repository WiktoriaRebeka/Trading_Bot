# orderflow_engine/msi_kline_feed.py
# Ciągłość oficjalnych świec 1M Bybit → MsiEngine (WS kline.confirm + REST gap-fill).

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MINUTE_MS = 60_000


class ContinuityKind(str, Enum):
    FIRST = "FIRST"
    CONTINUOUS = "CONTINUOUS"
    DUPLICATE = "DUPLICATE"
    LATE = "LATE"
    GAP = "GAP"


@dataclass
class MsiKlineStats:
    from_ws: int = 0
    from_gap_fill: int = 0
    duplicates: int = 0
    late: int = 0
    gaps: int = 0
    gap_candles_filled: int = 0

    def snapshot(self) -> Dict[str, int]:
        return {
            "from_ws": self.from_ws,
            "from_gap_fill": self.from_gap_fill,
            "duplicates": self.duplicates,
            "late": self.late,
            "gaps": self.gaps,
            "gap_candles_filled": self.gap_candles_filled,
        }


@dataclass
class ContinuityResult:
    kind: ContinuityKind
    missing_start_ts: Optional[int] = None  # inclusive first missing open
    missing_end_ts: Optional[int] = None  # inclusive last missing open (start - MINUTE_MS)


def classify_continuity(last_ts: Optional[int], candle_ts: int) -> ContinuityResult:
    """Porównaj open-time nowej świecy z last_msi_ts."""
    ts = int(candle_ts)
    if last_ts is None:
        return ContinuityResult(ContinuityKind.FIRST)
    last = int(last_ts)
    if ts == last:
        return ContinuityResult(ContinuityKind.DUPLICATE)
    if ts < last:
        return ContinuityResult(ContinuityKind.LATE)
    expected = last + MINUTE_MS
    if ts == expected:
        return ContinuityResult(ContinuityKind.CONTINUOUS)
    if ts > expected:
        return ContinuityResult(
            ContinuityKind.GAP,
            missing_start_ts=expected,
            missing_end_ts=ts - MINUTE_MS,
        )
    return ContinuityResult(ContinuityKind.LATE)


def parse_ws_kline_item(item: dict) -> Optional[dict]:
    """
    Mapuje element data[] z topic kline.1.* na dict świecy MSI.
    Zwraca None gdy confirm != true lub brak wymaganych pól.
    """
    if not isinstance(item, dict):
        return None
    if item.get("confirm") is not True:
        return None
    try:
        start = int(item["start"])
        return {
            "ts": start,
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(item.get("volume") or 0.0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def candle_dict_from_ohlcv(
    ts: int, open_: float, high: float, low: float, close: float, volume: float = 0.0
) -> dict:
    return {
        "ts": int(ts),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


@dataclass
class MsiKlineFeedState:
    """Per-processor stan ciągłości MSI kline."""

    last_ts: Dict[str, int] = field(default_factory=dict)
    live_ready: Dict[str, bool] = field(default_factory=dict)
    stats: MsiKlineStats = field(default_factory=MsiKlineStats)
    _last_summary_mono: float = field(default_factory=time.monotonic)
    summary_interval_sec: float = 3600.0

    def mark_bootstrap_done(self, symbol: str, last_candle_ts: int) -> None:
        sym = str(symbol).upper().replace(".P", "")
        self.last_ts[sym] = int(last_candle_ts)
        self.live_ready[sym] = True
        logger.info(
            "[MSI-KLINE] %s bootstrap done last_msi_ts=%s live_ready=1",
            sym,
            last_candle_ts,
        )

    def maybe_log_summary(self) -> None:
        now = time.monotonic()
        if now - self._last_summary_mono < self.summary_interval_sec:
            return
        self._last_summary_mono = now
        s = self.stats.snapshot()
        logger.info(
            "[MSI-KLINE] summary 60m: ws=%s gap_fill=%s gap_events=%s "
            "gap_candles=%s dup=%s late=%s ready_symbols=%s",
            s["from_ws"],
            s["from_gap_fill"],
            s["gaps"],
            s["gap_candles_filled"],
            s["duplicates"],
            s["late"],
            sum(1 for v in self.live_ready.values() if v),
        )


async def fetch_gap_candles(
    backfiller: Any,
    symbol: str,
    *,
    missing_start_ts: int,
    missing_end_ts: int,
    rate_limiter: Any = None,
) -> List[dict]:
    """
    Pobierz zamknięte M1 od missing_start_ts do missing_end_ts włącznie (open times).
    """
    if missing_end_ts < missing_start_ts:
        return []
    total = int((missing_end_ts - missing_start_ts) // MINUTE_MS) + 1
    result = await backfiller.fetch_klines_1m_paginated(
        symbol,
        end_ms=int(missing_end_ts),
        total_candles=total,
        rate_limiter=rate_limiter,
    )
    rows = list(result.rows or [])
    # Zawęż do dokładnego zakresu (paginacja może zwrócić więcej / mniej na brzegach).
    filtered = [r for r in rows if missing_start_ts <= int(r["ts"]) <= missing_end_ts]
    filtered.sort(key=lambda r: int(r["ts"]))
    return filtered
