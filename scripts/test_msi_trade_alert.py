#!/usr/bin/env python3
"""Test budowy alertu MSI OB (bez wysyłki na Bybit)."""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, OrderBlock, StructureEvent
from orderflow_engine.msi_trade_signal import build_msi_ob_alert, msi_order_event_id


def _ob(direction: str, low: float, high: float) -> OrderBlock:
    return OrderBlock(
        chain_id="BTCUSDT-1700000000000-1",
        candle=MsiCandle(ts=1_700_000_000_000, open=low, high=high, low=low, close=high),
        direction=direction,
        hl_lh_level=(low + high) / 2,
        bos_level=high,
        liquidity_level=low,
        initial_trend="UP" if direction == "LONG" else "DOWN",
        detected_at_ts=1_700_000_060_000,
    )


def test_long_alert_geometry():
    ob = _ob("LONG", 100.0, 100.5)
    ev = StructureEvent("OB_NEW", "BTCUSDT", ob.detected_at_ts, {})
    alert = build_msi_ob_alert(ob, ev, processor=None)
    assert alert is not None
    assert alert["signal_mode"] == "msi_orderblock"
    assert alert["order_type"] == "Limit"
    assert alert["time_in_force"] == "GTC"
    assert alert["entry"] == 100.5
    assert alert["sl"] == 100.0
    assert math.isclose(alert["tp"], 101.5)
    assert alert["event_id"] == msi_order_event_id(ob.chain_id)


def test_short_alert_geometry():
    ob = _ob("SHORT", 99.0, 99.8)
    ev = StructureEvent("OB_NEW", "ETHUSDT", ob.detected_at_ts, {})
    alert = build_msi_ob_alert(ob, ev, processor=None)
    assert alert is not None
    assert alert["entry"] == 99.0
    assert alert["sl"] == 99.8
    assert math.isclose(alert["tp"], 97.4)


def test_tight_ob_rejected():
    ob = _ob("LONG", 100.0, 100.1)
    ev = StructureEvent("OB_NEW", "BTCUSDT", ob.detected_at_ts, {})
    assert build_msi_ob_alert(ob, ev, processor=None) is None


if __name__ == "__main__":
    test_long_alert_geometry()
    test_short_alert_geometry()
    test_tight_ob_rejected()
    print("OK — test_msi_trade_alert")
