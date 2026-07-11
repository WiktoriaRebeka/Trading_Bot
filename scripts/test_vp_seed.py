#!/usr/bin/env python3
"""Testy VP seed: bulk merge, dedupe live+REST, vp_seed_failed flag."""

import os
os.environ.setdefault("MSI_LOG_TO_BQ", "false")

import sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.ob_orderflow_snapshot import (
    VP_HISTORY_MAXLEN,
    VP_LOOKBACK_CANDLES,
    merge_vol_candles,
    build_ob_vp_context,
    normalize_vol_candle,
)
from orderflow_engine.metrics_processor import OrderFlowMetrics


def _make_rows(n: int, start_ts: int = 0, base_vol: float = 10.0):
    return [
        {
            "ts": start_ts + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": base_vol + i,
        }
        for i in range(n)
    ]


def test_bulk_seed_keeps_tail_not_first_appends():
    p = OrderFlowMetrics()
    rows = _make_rows(400, start_ts=1_000_000)
    ok = p.seed_vp_buffer("BTCUSDT", rows)
    assert ok is True
    assert p.is_vp_seed_ready("BTCUSDT")
    buf = list(p.candle_vol_history["BTCUSDT"])
    assert len(buf) == VP_HISTORY_MAXLEN
    assert buf[0]["ts"] == rows[-VP_HISTORY_MAXLEN]["ts"]
    assert buf[-1]["ts"] == rows[-1]["ts"]


def test_merge_live_rest_dedupe_prefers_higher_volume():
    existing = [normalize_vol_candle({"ts": 1000, "low": 99, "high": 101, "volume": 5.0})]
    incoming = [normalize_vol_candle({"ts": 1000, "low": 99, "high": 101, "volume": 50.0})]
    merged = merge_vol_candles(existing, incoming)
    assert len(merged) == 1
    assert merged[0]["volume"] == 50.0


def test_live_merge_after_seed_preserves_rest_and_adds_new_ts():
    p = OrderFlowMetrics()
    p.seed_vp_buffer("ETHUSDT", _make_rows(VP_LOOKBACK_CANDLES + 50, start_ts=0))
    last_ts = list(p.candle_vol_history["ETHUSDT"])[-1]["ts"]
    p.merge_live_vol_candle(
        "ETHUSDT",
        {"ts": last_ts + 60_000, "low": 98, "high": 102, "close": 100, "volume": 77.0},
    )
    buf = list(p.candle_vol_history["ETHUSDT"])
    assert buf[-1]["ts"] == last_ts + 60_000
    assert buf[-1]["volume"] == 77.0
    assert len(buf) <= VP_HISTORY_MAXLEN


def test_vp_seed_failed_flag_pending_and_ready():
    p = OrderFlowMetrics()
    p.vp_seed_status["BTCUSDT"] = "pending"
    ctx_pending = build_ob_vp_context(p, "BTCUSDT", 101.0, 99.0, 9_999_999_999)
    assert ctx_pending["vp_seed_failed"] is True

    p.seed_vp_buffer("BTCUSDT", _make_rows(VP_LOOKBACK_CANDLES, start_ts=0))
    ctx_ready = build_ob_vp_context(
        p, "BTCUSDT", 101.0, 99.0, _make_rows(VP_LOOKBACK_CANDLES + 5, start_ts=0)[-1]["ts"] + 60_000
    )
    assert ctx_ready["vp_seed_failed"] is False
    assert ctx_ready["vp_window_candles"] >= VP_LOOKBACK_CANDLES


def test_mark_failed_sets_flag():
    p = OrderFlowMetrics()
    p.mark_vp_seed_failed("SOLUSDT")
    ctx = build_ob_vp_context(p, "SOLUSDT", 10.0, 9.0, 1_000_000)
    assert ctx["vp_seed_failed"] is True


def test_vp_reliable_false_with_short_window():
    """Replay / live przed pełnym oknem — status seedu może być ready, pomiast nie."""
    p = OrderFlowMetrics()
    rows = _make_rows(8, start_ts=1_000_000)
    p.seed_vp_buffer("BTCUSDT", rows)
    p.vp_seed_status["BTCUSDT"] = "ready"
    ob_ts = rows[-1]["ts"] + 60_000
    ctx = build_ob_vp_context(p, "BTCUSDT", 101.0, 99.0, ob_ts)
    assert ctx["vp_window_candles"] == 8
    assert ctx["vp_reliable"] is False


def test_vp_reliable_true_with_full_window():
    p = OrderFlowMetrics()
    rows = _make_rows(VP_LOOKBACK_CANDLES + 10, start_ts=1_000_000)
    p.seed_vp_buffer("BTCUSDT", rows)
    ob_ts = rows[-1]["ts"] + 60_000
    ctx = build_ob_vp_context(p, "BTCUSDT", 101.0, 99.0, ob_ts)
    assert ctx["vp_window_candles"] >= VP_LOOKBACK_CANDLES
    assert ctx["ob_zone_in_window"] is True
    assert ctx["ob_vp_ratio"] is not None
    assert ctx["vp_reliable"] is True


if __name__ == "__main__":
    test_bulk_seed_keeps_tail_not_first_appends()
    test_merge_live_rest_dedupe_prefers_higher_volume()
    test_live_merge_after_seed_preserves_rest_and_adds_new_ts()
    test_vp_seed_failed_flag_pending_and_ready()
    test_mark_failed_sets_flag()
    test_vp_reliable_false_with_short_window()
    test_vp_reliable_true_with_full_window()
    print("OK — test_vp_seed")
