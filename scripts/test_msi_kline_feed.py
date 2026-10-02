#!/usr/bin/env python3
"""Unit tests: MSI kline continuity + confirm filter."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SIGNAL_MODE", "msi_orderblock")
os.environ["MSI_ENGINE_ENABLED"] = "true"
os.environ.setdefault("MSI_TRADE_ENABLED", "false")
os.environ.setdefault("MSI_LOG_TO_BQ", "false")

# Python 3.14 + protobuf w tym venv psuje import google.cloud — stub na czas testów.
import types
from unittest.mock import MagicMock

for _name in (
    "google",
    "google.cloud",
    "google.cloud.bigquery",
    "google.api_core",
    "google.protobuf",
):
    sys.modules.setdefault(_name, MagicMock())

from orderflow_engine.msi_kline_feed import (
    ContinuityKind,
    MINUTE_MS,
    classify_continuity,
    fetch_gap_candles,
    parse_ws_kline_item,
)
from orderflow_engine import metrics_processor as mp

mp.MSI_ENGINE_ENABLED = True
from orderflow_engine.metrics_processor import OrderFlowMetrics


class _FakeResult:
    def __init__(self, rows: List[dict]):
        self.rows = rows
        self.ok = True
        self.rate_limited = False


class _FakeBackfiller:
    def __init__(self, rows: List[dict]):
        self._rows = rows
        self.calls = []

    async def fetch_klines_1m_paginated(self, symbol, *, end_ms, total_candles, rate_limiter=None):
        self.calls.append(
            {"symbol": symbol, "end_ms": end_ms, "total_candles": total_candles}
        )
        return _FakeResult(list(self._rows))


def test_confirm_false_ignored() -> None:
    assert (
        parse_ws_kline_item(
            {
                "confirm": False,
                "start": 1,
                "open": "1",
                "high": "2",
                "low": "0.5",
                "close": "1.5",
                "volume": "1",
            }
        )
        is None
    )
    assert parse_ws_kline_item(
        {
            "confirm": True,
            "start": 1000,
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": "1.5",
            "volume": "3",
        }
    ) == {
        "ts": 1000,
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 3.0,
    }
    print("OK  confirm=false ignored; confirm=true parsed")


def test_classify_continuity() -> None:
    t0 = 1_700_000_000_000
    assert classify_continuity(None, t0).kind == ContinuityKind.FIRST
    assert classify_continuity(t0, t0 + MINUTE_MS).kind == ContinuityKind.CONTINUOUS
    assert classify_continuity(t0, t0).kind == ContinuityKind.DUPLICATE
    assert classify_continuity(t0, t0 - MINUTE_MS).kind == ContinuityKind.LATE
    gap = classify_continuity(t0, t0 + 4 * MINUTE_MS)
    assert gap.kind == ContinuityKind.GAP
    assert gap.missing_start_ts == t0 + MINUTE_MS
    assert gap.missing_end_ts == t0 + 3 * MINUTE_MS
    print("OK  classify continuous / gap-3 / dup / late")


async def _run_processor_continuity() -> None:
    proc = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
    sym = "BTCUSDT"
    t0 = 1_700_000_000_000
    candles = []
    for i in range(5):
        candles.append(
            {
                "ts": t0 + i * MINUTE_MS,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1.0,
            }
        )

    proc._msi_kline.mark_bootstrap_done(sym, candles[0]["ts"])
    fed: List[int] = []

    def _feed(symbol, candle):
        fed.append(int(candle["ts"]))

    proc._feed_msi_candle = _feed  # type: ignore[method-assign]

    await proc.process_closed_kline_1m(sym, candles[1], source="ws")
    assert fed == [candles[1]["ts"]], fed
    assert proc._msi_kline.stats.from_ws == 1

    await proc.process_closed_kline_1m(sym, candles[1], source="ws")
    assert fed == [candles[1]["ts"]]
    assert proc._msi_kline.stats.duplicates == 1

    await proc.process_closed_kline_1m(sym, candles[0], source="ws")
    assert proc._msi_kline.stats.late == 1

    gap_rows = [candles[2], candles[3]]
    fake = _FakeBackfiller(gap_rows)

    class _VP:
        backfiller = fake
        rate_limiter = None

    proc.vp_fetcher = _VP()
    await proc.process_closed_kline_1m(sym, candles[4], source="ws")
    assert fed == [
        candles[1]["ts"],
        candles[2]["ts"],
        candles[3]["ts"],
        candles[4]["ts"],
    ], fed
    assert proc._msi_kline.stats.gaps == 1
    assert proc._msi_kline.stats.from_gap_fill == 2
    assert proc._msi_kline.stats.from_ws == 2
    assert fake.calls and fake.calls[0]["total_candles"] == 2
    print("OK  processor continuous / dup / late / gap-3 fill")


def test_processor_continuity() -> None:
    asyncio.run(_run_processor_continuity())


def test_fetch_gap_candles_filter() -> None:
    t0 = 1_700_000_000_000
    rows = [
        {"ts": t0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"ts": t0 + MINUTE_MS, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"ts": t0 + 2 * MINUTE_MS, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"ts": t0 + 3 * MINUTE_MS, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    fake = _FakeBackfiller(rows)

    async def _run():
        got = await fetch_gap_candles(
            fake,
            "BTCUSDT",
            missing_start_ts=t0 + MINUTE_MS,
            missing_end_ts=t0 + 2 * MINUTE_MS,
        )
        assert [r["ts"] for r in got] == [t0 + MINUTE_MS, t0 + 2 * MINUTE_MS]

    asyncio.run(_run())
    print("OK  fetch_gap_candles range filter")


def main() -> None:
    test_confirm_false_ignored()
    test_classify_continuity()
    test_fetch_gap_candles_filter()
    test_processor_continuity()
    print("ALL unit MSI kline tests PASSED")


if __name__ == "__main__":
    main()
