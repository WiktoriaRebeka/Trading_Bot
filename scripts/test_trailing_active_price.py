#!/usr/bin/env python3
"""Testy korekty activePrice trailingu względem Bybit (LONG/SHORT)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared_lib.ob_execution import adjust_trailing_active_price


def test_short_active_below_last_when_past_2r():
    # AAVE case: entry~82.98, 2R active~81.85, last~80.89 — active musi spaść poniżej last
    out = adjust_trailing_active_price(
        active_price=81.85,
        real_entry=82.98,
        is_long=False,
        market_price=80.89,
        tick=0.01,
    )
    assert out is not None
    assert out < 80.89
    assert out < 82.98


def test_short_active_unchanged_before_2r():
    out = adjust_trailing_active_price(
        active_price=81.50,
        real_entry=82.98,
        is_long=False,
        market_price=83.10,
        tick=0.01,
    )
    assert out == 81.50


def test_long_active_above_last_when_past_2r():
    out = adjust_trailing_active_price(
        active_price=101.0,
        real_entry=100.0,
        is_long=True,
        market_price=102.5,
        tick=0.1,
    )
    assert out is not None
    assert out > 102.5
    assert out > 100.0


def test_long_active_unchanged_before_2r():
    out = adjust_trailing_active_price(
        active_price=101.0,
        real_entry=100.0,
        is_long=True,
        market_price=100.5,
        tick=0.1,
    )
    assert out == 101.0


if __name__ == "__main__":
    test_short_active_below_last_when_past_2r()
    test_short_active_unchanged_before_2r()
    test_long_active_above_last_when_past_2r()
    test_long_active_unchanged_before_2r()
    print("OK — test_trailing_active_price")
