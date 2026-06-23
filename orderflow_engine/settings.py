# orderflow_engine/settings.py
"""Orderflow signal filters — override via environment variables."""

import os
from datetime import datetime, timezone
from typing import List


def _parse_skip_sessions(raw: str | None) -> List[str]:
    if raw is None or not str(raw).strip():
        return []
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


class OrderflowSettings:
    """Signal filter toggles (env-configurable)."""

    SKIP_SESSIONS: List[str]
    REQUIRE_ZERO_DELTA: bool
    LOG_EVAL_VERBOSE: bool

    def __init__(self) -> None:
        self.SKIP_SESSIONS = _parse_skip_sessions(os.environ.get("SKIP_SESSIONS"))
        self.REQUIRE_ZERO_DELTA = _parse_bool(os.environ.get("REQUIRE_ZERO_DELTA"), True)
        # Dev-only: LOG_EVAL_VERBOSE=true restores per-filter debug lines in evaluate_and_maybe_alert.
        self.LOG_EVAL_VERBOSE = _parse_bool(os.environ.get("LOG_EVAL_VERBOSE"), False)


settings = OrderflowSettings()


def get_trading_session(dt: datetime | None = None) -> str:
    """UTC hour → session name (same boundaries as bigquery_logger._get_session)."""
    dt = dt or datetime.now(timezone.utc)
    hour = dt.hour
    if 0 <= hour < 7:
        return "ASIA"
    if hour < 15:
        return "LONDON"
    if hour < 21:
        return "NY"
    return "AFTERHOURS"
