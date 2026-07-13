#!/usr/bin/env python3
"""Testy VP z API: dual horizon, VA, 3-bin SL/entry, paginacja, raw_context."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.ob_orderflow_snapshot import (
    VP_BINS_LONG,
    VP_BINS_SHORT,
    VP_PERIOD_LONG,
    VP_PERIOD_SHORT,
    _compute_value_area,
    _level_bin_indices,
    build_dual_vp_context,
    compute_horizon_vp,
    empty_dual_vp_context,
)
from shared_lib.orderblock_bq import insert_orderblock_rows, sanitize_raw_context


def _make_candles(n: int, *, start_ts: int = 1_000_000, base: float = 100.0, vol: float = 10.0):
    rows = []
    for i in range(n):
        lo = base + i * 0.01
        hi = lo + 0.5
        rows.append({
            "ts": start_ts + i * 60_000,
            "low": lo,
            "high": hi,
            "volume": vol,
        })
    return rows


def test_short_is_suffix_of_long():
    candles = _make_candles(VP_PERIOD_LONG, vol=5.0)
    ctx = build_dual_vp_context(
        candles,
        ob_high=100.5,
        ob_low=100.0,
        entry=100.5,
        sl=100.0,
    )
    assert ctx["vp5h_vp_candles_used"] == VP_PERIOD_SHORT
    assert ctx["vp24h_vp_candles_used"] == VP_PERIOD_LONG
    assert ctx["vp5h_vp_reliable"] is True
    assert ctx["vp24h_vp_reliable"] is True
    assert ctx["vp5h_vp_median_bin_vol"] is not None
    assert ctx["vp5h_ob_zone_bin_vol_avg"] is not None
    assert ctx["vp5h_sl_bin_vol_avg"] is not None
    assert ctx["vp5h_entry_bin_vol_avg"] is not None
    assert ctx["vp5h_vp_bin_width"] is not None
    assert ctx["vp5h_ob_zone_bins_covered"] >= 1


def test_vp_reliable_false_on_short_window():
    candles = _make_candles(50)
    ctx = build_dual_vp_context(
        candles,
        ob_high=100.5,
        ob_low=100.0,
        entry=100.5,
        sl=100.0,
    )
    assert ctx["vp5h_vp_reliable"] is False
    assert ctx["vp24h_vp_reliable"] is False


def test_value_area_expands_from_poc():
    bins = [1.0, 5.0, 3.0, 2.0, 1.0]
    va_low, va_high = _compute_value_area(bins, p_min=0.0, bin_w=1.0, n_bins=5, poc_idx=1, total_vol=12.0)
    assert va_low is not None and va_high is not None
    assert va_low <= 1.0
    assert va_high >= 3.0


def test_level_bin_window_three_bins():
    indices = _level_bin_indices(1.5, p_min=0.0, bin_w=1.0, n_bins=5)
    assert indices == [0, 1, 2]


def test_ob_outside_va():
    candles = _make_candles(400, base=200.0, vol=20.0)
    ctx = build_dual_vp_context(
        candles,
        ob_high=100.5,
        ob_low=100.0,
        entry=100.5,
        sl=100.0,
    )
    assert isinstance(ctx["vp5h_ob_outside_va"], bool)


def test_empty_dual_vp_has_all_keys():
    ctx = empty_dual_vp_context()
    for prefix in ("vp5h_", "vp24h_"):
        for key in (
            "poc_price", "va_high", "va_low", "ob_vp_ratio", "ob_outside_va",
            "ob_dist_from_poc_pct", "sl_vp_ratio", "entry_vp_ratio",
            "sl_outside_va", "entry_outside_va", "vp_candles_used", "vp_reliable",
            "vp_median_bin_vol", "ob_zone_bin_vol_avg", "sl_bin_vol_avg",
            "entry_bin_vol_avg", "vp_bin_width", "vp_price_min", "vp_price_max",
            "ob_zone_bins_covered",
        ):
            assert f"{prefix}{key}" in ctx
    assert ctx["vp_bins_short"] == VP_BINS_SHORT
    assert ctx["vp_bins_long"] == VP_BINS_LONG


def test_dual_vp_raw_context_serializes_for_bq():
    ctx = empty_dual_vp_context()
    ctx.update({
        "vp5h_ob_vp_ratio": 1.23456789,
        "vp24h_sl_vp_ratio": 0.5,
        "vp5h_vp_reliable": True,
        "vp24h_ob_outside_va": False,
    })
    sanitized = sanitize_raw_context(ctx)
    assert isinstance(sanitized, dict)
    client = MagicMock()
    client.insert_rows_json.return_value = []
    insert_orderblock_rows(
        client,
        "proj.ds.orderblock_events",
        [{
            "event_id": "e1",
            "event_type": "OB_NEW",
            "symbol": "BTCUSDT",
            "event_ts": "2024-01-15T10:30:00Z",
            "raw_context": sanitized,
        }],
    )
    sent = client.insert_rows_json.call_args[0][1][0]
    assert isinstance(sent["raw_context"], str)
    parsed = json.loads(sent["raw_context"])
    assert parsed["vp5h_ob_vp_ratio"] == 1.23456789


def test_paginated_fetch_merges_two_pages():
    async def _run():
        from orderflow_engine.backfiller import HistoryBackfiller

        end_ms = 1_700_000_000_000
        page1 = []
        for i in range(1000):
            ts = end_ms - i * 60_000
            page1.append([str(ts), "1", "2", "1", "1.5", "10"])
        page2 = []
        for i in range(440):
            ts = end_ms - (1000 + i) * 60_000
            page2.append([str(ts), "1", "2", "1", "1.5", "10"])

        responses = [
            {"retCode": 0, "result": {"list": page1}},
            {"retCode": 0, "result": {"list": page2}},
        ]
        call_idx = {"n": 0}

        class FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                idx = call_idx["n"]
                call_idx["n"] += 1
                return responses[min(idx, len(responses) - 1)]

        session = MagicMock()
        session.get = MagicMock(side_effect=lambda *a, **k: FakeResponse())

        backfiller = HistoryBackfiller()
        backfiller._session = session
        result = await backfiller.fetch_klines_1m_paginated(
            "BTCUSDT", end_ms=end_ms, total_candles=1440,
        )
        assert result.ok is True
        assert len(result.rows) == 1440
        assert result.rows[0]["ts"] < result.rows[-1]["ts"]
        assert result.rows[-1]["ts"] == end_ms
        assert session.get.call_count >= 2

    import asyncio
    asyncio.run(_run())


def test_horizon_diagnostic_fields_present():
    candles = _make_candles(VP_PERIOD_SHORT)
    ctx = compute_horizon_vp(
        candles,
        ob_high=100.5,
        ob_low=100.0,
        entry=100.5,
        sl=100.0,
        required_candles=VP_PERIOD_SHORT,
        n_bins=VP_BINS_SHORT,
        prefix="vp5h_",
    )
    assert ctx["vp5h_vp_price_min"] is not None
    assert ctx["vp5h_vp_price_max"] is not None
    if ctx["vp5h_ob_vp_ratio"] is not None:
        assert ctx["vp5h_ob_zone_bin_vol_avg"] is not None
        assert ctx["vp5h_vp_median_bin_vol"] is not None


if __name__ == "__main__":
    test_short_is_suffix_of_long()
    test_vp_reliable_false_on_short_window()
    test_value_area_expands_from_poc()
    test_level_bin_window_three_bins()
    test_ob_outside_va()
    test_empty_dual_vp_has_all_keys()
    test_dual_vp_raw_context_serializes_for_bq()
    test_paginated_fetch_merges_two_pages()
    test_horizon_diagnostic_fields_present()
    print("OK — test_vp_api")
