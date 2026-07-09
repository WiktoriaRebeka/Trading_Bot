#!/usr/bin/env python3
"""Testy logiki karencji MSI (failed break na świecach 1M)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot_service.msi_cooldown_logic import cooldown_failed_break, parse_signal_ts


def test_long_failed_break_when_low_touches_entry():
    klines = [
        {"ts": 1, "open": 101, "high": 102, "low": 100, "close": 101},
        {"ts": 2, "open": 101, "high": 101.5, "low": 99.5, "close": 100},
    ]
    failed, min_low, max_high = cooldown_failed_break("LONG", 100.0, klines)
    assert failed is True
    assert min_low == 99.5
    assert max_high == 102


def test_long_ok_when_low_above_entry():
    klines = [
        {"ts": 1, "open": 101, "high": 102, "low": 100.5, "close": 101},
    ]
    failed, _, _ = cooldown_failed_break("LONG", 100.0, klines)
    assert failed is False


def test_short_failed_break_when_high_touches_entry():
    klines = [
        {"ts": 1, "open": 99, "high": 100.5, "low": 98, "close": 99},
    ]
    failed, _, max_high = cooldown_failed_break("SHORT", 100.0, klines)
    assert failed is True
    assert max_high == 100.5


def test_short_ok_when_high_below_entry():
    klines = [
        {"ts": 1, "open": 99, "high": 99.5, "low": 98, "close": 99},
    ]
    failed, _, _ = cooldown_failed_break("SHORT", 100.0, klines)
    assert failed is False


def test_long_signal_candle_touch_ignored_for_reject():
    """Knot na świecy sygnałowej nie odrzuca — tylko pełne okno do logu."""
    signal_ts_ms = 30_000  # w świecy ts=0
    klines = [
        {"ts": 0, "open": 101, "high": 102, "low": 99.0, "close": 101},
        {"ts": 60_000, "open": 101, "high": 102, "low": 100.5, "close": 101},
    ]
    failed, min_low, max_high = cooldown_failed_break(
        "LONG", 100.0, klines, signal_ts_ms=signal_ts_ms
    )
    assert failed is False
    assert min_low == 99.0
    assert max_high == 102


def test_long_failed_break_after_signal_candle_close():
    signal_ts_ms = 30_000
    klines = [
        {"ts": 0, "open": 101, "high": 102, "low": 99.0, "close": 101},
        {"ts": 60_000, "open": 101, "high": 102, "low": 99.5, "close": 101},
    ]
    failed, min_low, _ = cooldown_failed_break(
        "LONG", 100.0, klines, signal_ts_ms=signal_ts_ms
    )
    assert failed is True
    assert min_low == 99.0


def test_parse_signal_ts_z_suffix():
    dt = parse_signal_ts("2026-01-15T12:00:00Z")
    assert dt.year == 2026
    assert dt.hour == 12
    assert dt.tzinfo is not None


if __name__ == "__main__":
    test_long_failed_break_when_low_touches_entry()
    test_long_ok_when_low_above_entry()
    test_short_failed_break_when_high_touches_entry()
    test_short_ok_when_high_below_entry()
    test_long_signal_candle_touch_ignored_for_reject()
    test_long_failed_break_after_signal_candle_close()
    test_parse_signal_ts_z_suffix()
    print("OK — test_msi_cooldown")
