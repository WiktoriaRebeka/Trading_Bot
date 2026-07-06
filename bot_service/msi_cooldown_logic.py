# bot_service/msi_cooldown_logic.py
# Czysta logika karencji MSI (bez zależności Flask/Bybit).

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MSI_COOLDOWN_MINUTES = int(os.environ.get("MSI_COOLDOWN_MINUTES", "30"))


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
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    LONG: min(low) <= entry_limit → failed break.
    SHORT: max(high) >= entry_limit → failed break.
    Zwraca (failed, min_low, max_high).
    """
    if not klines:
        return False, None, None
    min_low = min(k["low"] for k in klines)
    max_high = max(k["high"] for k in klines)
    is_long = str(direction).upper() == "LONG"
    if is_long:
        return min_low <= entry_limit, min_low, max_high
    return max_high >= entry_limit, min_low, max_high
