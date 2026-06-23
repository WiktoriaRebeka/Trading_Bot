# shared_lib/signal_mode.py
# Feature flag: SIGNAL_MODE=msi_orderblock | footprint_hunter

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SIGNAL_MODE_MSI = "msi_orderblock"
SIGNAL_MODE_FOOTPRINT = "footprint_hunter"

_ALIASES = {
    "msi": SIGNAL_MODE_MSI,
    "msi_ob": SIGNAL_MODE_MSI,
    "orderblock": SIGNAL_MODE_MSI,
    "footprint": SIGNAL_MODE_FOOTPRINT,
    "legacy": SIGNAL_MODE_FOOTPRINT,
    "hunter": SIGNAL_MODE_FOOTPRINT,
}


def normalize_signal_mode(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return SIGNAL_MODE_MSI
    key = str(raw).strip().lower()
    if key in (SIGNAL_MODE_MSI, SIGNAL_MODE_FOOTPRINT):
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    logger.warning(
        "Nieznany SIGNAL_MODE=%r — używam domyślnego %s",
        raw,
        SIGNAL_MODE_MSI,
    )
    return SIGNAL_MODE_MSI


def get_signal_mode() -> str:
    return normalize_signal_mode(os.environ.get("SIGNAL_MODE"))


def is_msi_orderblock_mode() -> bool:
    return get_signal_mode() == SIGNAL_MODE_MSI


def is_footprint_hunter_mode() -> bool:
    return get_signal_mode() == SIGNAL_MODE_FOOTPRINT


def env_flag(name: str, default_when_unset: bool) -> bool:
    """Zmienna env nadpisuje domyślną logikę z SIGNAL_MODE."""
    raw = os.environ.get(name)
    if raw is None:
        return default_when_unset
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def msi_engine_enabled() -> bool:
    return env_flag("MSI_ENGINE_ENABLED", is_msi_orderblock_mode())


def msi_trade_enabled() -> bool:
    return env_flag("MSI_TRADE_ENABLED", is_msi_orderblock_mode())


def footprint_alerts_enabled() -> bool:
    return env_flag("FOOTPRINT_ALERTS_ENABLED", is_footprint_hunter_mode())
