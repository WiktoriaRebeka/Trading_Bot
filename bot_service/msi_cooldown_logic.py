# bot_service/msi_cooldown_logic.py
# Czysta logika karencji MSI (bez zależności Flask/Bybit).

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MSI_COOLDOWN_MINUTES = int(os.environ.get("MSI_COOLDOWN_MINUTES", "30"))
M1_INTERVAL_MS = 60_000


def signal_candle_start_ms(signal_ts_ms: int) -> int:
    """Początek świecy 1M zawierającej timestamp sygnału (ms epoch UTC)."""
    return (int(signal_ts_ms) // M1_INTERVAL_MS) * M1_INTERVAL_MS


def klines_after_signal_candle_close(
    klines: List[Dict[str, Any]],
    signal_ts_ms: int,
) -> List[Dict[str, Any]]:
    """Świece 1M ze startem ts ściśle po zamknięciu świecy sygnałowej."""
    cutoff = signal_candle_start_ms(signal_ts_ms) + M1_INTERVAL_MS
    return [k for k in klines if int(k["ts"]) >= cutoff]


def parse_signal_ts(value: Any) -> datetime:
    """Parsuje timestamp_signal z payloadu alertu (ISO / Z)."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cooldown_failed_break(
    direction: str,
    entry_limit: float,
    klines: List[Dict[str, Any]],
    *,
    signal_ts_ms: Optional[int] = None,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    LONG: min(low) <= entry_limit → failed break.
    SHORT: max(high) >= entry_limit → failed break.

    Decyzja reject: tylko świece po zamknięciu świecy sygnałowej (pomija knot BOS).
    min_low / max_high w zwrotce: z pełnego okna [timestamp_signal, now] — do logu BQ.
    """
    if not klines:
        return False, None, None

    min_low = min(k["low"] for k in klines)
    max_high = max(k["high"] for k in klines)

    decision_klines = klines
    if signal_ts_ms is not None:
        decision_klines = klines_after_signal_candle_close(klines, signal_ts_ms)

    if not decision_klines:
        return False, min_low, max_high

    dec_min_low = min(k["low"] for k in decision_klines)
    dec_max_high = max(k["high"] for k in decision_klines)
    is_long = str(direction).upper() == "LONG"
    if is_long:
        return dec_min_low <= entry_limit, min_low, max_high
    return dec_max_high >= entry_limit, min_low, max_high
