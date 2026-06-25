#!/usr/bin/env python3
"""Testy sanitizacji orderblock_events (NUMERIC 8 miejsc, raw_context dict)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared_lib.orderblock_bq import (
    outcome_from_net_pnl,
    round_bq_float,
    sanitize_orderblock_row,
    sanitize_raw_context,
)


def test_round_bq_float_8_places():
    assert round_bq_float(1.123456789) == 1.12345679


def test_raw_context_parses_json_string():
    ctx = sanitize_raw_context('{"a": 1, "nested": {"x": 1.111111111}}')
    assert isinstance(ctx, dict)
    assert ctx["a"] == 1
    assert ctx["nested"]["x"] == 1.11111111


def test_raw_context_invalid_string():
    assert sanitize_raw_context("not-json") is None


def test_raw_context_keeps_dict():
    ctx = sanitize_raw_context({"fee": 0.00075, "nested": {"x": 1.111111111}})
    assert isinstance(ctx, dict)
    assert ctx["nested"]["x"] == 1.11111111


def test_sanitize_orderblock_row():
    row = sanitize_orderblock_row({
        "event_id": "e1",
        "event_type": "OB_NEW",
        "symbol": "BTCUSDT",
        "event_ts": "2024-01-15T10:30:00Z",
        "ob_high": 100.123456789,
        "risk_ob": 0.500000001,
        "raw_context": {"chain_id": "BTCUSDT-1", "meta": 1.999999999},
        "dom_wall": True,
    })
    assert row["ob_high"] == 100.12345679
    assert row["risk_ob"] == 0.5
    assert isinstance(row["raw_context"], dict)
    assert row["raw_context"]["meta"] == 2.0


def test_order_placed_row_raw_context_dict():
    row = sanitize_orderblock_row({
        "event_id": "e2",
        "event_type": "ORDER_PLACED",
        "symbol": "AVAXUSDT",
        "event_ts": "2024-01-15T10:30:00Z",
        "chain_id": "AVAXUSDT-1782337020000-1",
        "ob_id": "AVAXUSDT-1782337020000-1",
        "ob_direction": "SHORT",
        "entry_limit": 6.356,
        "sl": 6.384,
        "risk_ob": 0.028,
        "trade_event_id": "AVAXUSDT-1782337020000-1",
        "trade_order_id": "484af99b-9ca9-4115-93b2-e262a4c45673",
        "raw_context": {
            "source": "bot_service",
            "trade_event_id": "AVAXUSDT-1782337020000-1",
            "order_id": "484af99b-9ca9-4115-93b2-e262a4c45673",
        },
    })
    assert isinstance(row["raw_context"], dict)
    assert row["raw_context"]["source"] == "bot_service"


def test_outcome_from_pnl():
    assert outcome_from_net_pnl(1.5) == "WIN"
    assert outcome_from_net_pnl(-0.1) == "LOSS"
    assert outcome_from_net_pnl(0.0) == "BREAKEVEN"


if __name__ == "__main__":
    test_round_bq_float_8_places()
    test_raw_context_parses_json_string()
    test_raw_context_invalid_string()
    test_raw_context_keeps_dict()
    test_sanitize_orderblock_row()
    test_order_placed_row_raw_context_dict()
    test_outcome_from_pnl()
    print("OK — test_orderblock_bq")
