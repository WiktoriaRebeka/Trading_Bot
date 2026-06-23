#!/usr/bin/env python3
"""Testy entry/SL/sanity dla MSI OrderBlock."""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, OrderBlock
from shared_lib.ob_execution import (
    MIN_OB_HEIGHT_PCT,
    build_ob_trade_setup,
    compute_ob_entry_sl,
    passes_min_ob_height,
    validate_ob_sanity,
)


def _make_ob(direction: str, low: float, high: float) -> OrderBlock:
    return OrderBlock(
        chain_id="test-chain",
        candle=MsiCandle(ts=1_700_000_000_000, open=low, high=high, low=low, close=high),
        direction=direction,
        hl_lh_level=(low + high) / 2,
        bos_level=high,
        liquidity_level=low,
        initial_trend="UP" if direction == "LONG" else "DOWN",
        detected_at_ts=1_700_000_060_000,
    )


def test_long_entry_sl():
    ob = _make_ob("LONG", low=100.0, high=100.5)
    s = compute_ob_entry_sl(ob, symbol="BTCUSDT.P")
    assert s.direction == "LONG"
    assert s.entry_limit == 100.5
    assert s.sl == 100.0
    assert math.isclose(s.risk_ob, 0.5)
    ok, _ = validate_ob_sanity(s)
    assert ok


def test_short_entry_sl():
    ob = _make_ob("SHORT", low=99.0, high=99.8)
    s = compute_ob_entry_sl(ob, symbol="ETHUSDT.P")
    assert s.direction == "SHORT"
    assert s.entry_limit == 99.0
    assert s.sl == 99.8
    assert math.isclose(s.risk_ob, 0.8)
    ok, _ = validate_ob_sanity(s)
    assert ok


def test_sanity_rejects_wrong_side():
    # Sztucznie odwrócone poziomy — sanity musi odrzucić
    bad = compute_ob_entry_sl(_make_ob("LONG", low=100.0, high=100.5))
    from dataclasses import replace

    inverted = replace(bad, entry_limit=100.0, sl=100.5, risk_ob=0.5)
    ok, msg = validate_ob_sanity(inverted)
    assert not ok
    assert "LONG sanity fail" in msg


def test_min_height_filter():
  # 0.1% height — poniżej 0.2%
    ob = _make_ob("LONG", low=100.0, high=100.1)
    s = compute_ob_entry_sl(ob)
    ok, _ = passes_min_ob_height(s, MIN_OB_HEIGHT_PCT)
    assert not ok

    ob2 = _make_ob("LONG", low=100.0, high=100.3)
    s2 = compute_ob_entry_sl(ob2)
    ok2, _ = passes_min_ob_height(s2, MIN_OB_HEIGHT_PCT)
    assert ok2


def test_build_rejects_tight_ob():
    ob = _make_ob("SHORT", low=50_000.0, high=50_050.0)  # 0.1%
    assert build_ob_trade_setup(ob, symbol="BTCUSDT.P") is None


def test_build_accepts_valid_ob():
    ob = _make_ob("LONG", low=50_000.0, high=50_200.0)  # 0.4%
    s = build_ob_trade_setup(ob, symbol="BTCUSDT.P")
    assert s is not None
    assert math.isclose(s.risk_ob, 200.0)


if __name__ == "__main__":
    test_long_entry_sl()
    test_short_entry_sl()
    test_sanity_rejects_wrong_side()
    test_min_height_filter()
    test_build_rejects_tight_ob()
    test_build_accepts_valid_ob()
    print("OK — wszystkie testy ob_execution przeszły")
