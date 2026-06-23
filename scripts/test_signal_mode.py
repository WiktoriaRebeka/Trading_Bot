#!/usr/bin/env python3
"""Testy feature flag SIGNAL_MODE."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _reload():
    import importlib
    import shared_lib.signal_mode as sm

    return importlib.reload(sm)


def test_default_msi():
    os.environ.pop("SIGNAL_MODE", None)
    os.environ.pop("MSI_ENGINE_ENABLED", None)
    os.environ.pop("MSI_TRADE_ENABLED", None)
    os.environ.pop("FOOTPRINT_ALERTS_ENABLED", None)
    sm = _reload()
    assert sm.get_signal_mode() == sm.SIGNAL_MODE_MSI
    assert sm.is_msi_orderblock_mode()
    assert sm.msi_engine_enabled()
    assert sm.msi_trade_enabled()
    assert not sm.footprint_alerts_enabled()


def test_footprint_mode():
    os.environ["SIGNAL_MODE"] = "footprint_hunter"
    os.environ.pop("MSI_ENGINE_ENABLED", None)
    sm = _reload()
    assert sm.is_footprint_hunter_mode()
    assert not sm.msi_engine_enabled()
    assert sm.footprint_alerts_enabled()


def test_msi_env_override():
    os.environ["SIGNAL_MODE"] = "footprint_hunter"
    os.environ["MSI_ENGINE_ENABLED"] = "true"
    sm = _reload()
    assert sm.is_footprint_hunter_mode()
    assert sm.msi_engine_enabled()


if __name__ == "__main__":
    test_default_msi()
    test_footprint_mode()
    test_msi_env_override()
    print("OK — test_signal_mode")
