#!/usr/bin/env python3
"""
Integracja offline: bootstrap 280 + ciąg świec ścieżką process_closed_kline_1m
vs czysty REST _feed_msi_candle → identyczne OB_NEW / CHOCH (BTC, DOGE).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SIGNAL_MODE", "msi_orderblock")
os.environ["MSI_ENGINE_ENABLED"] = "true"
os.environ.setdefault("MSI_TRADE_ENABLED", "false")
os.environ.setdefault("MSI_LOG_TO_BQ", "false")

from unittest.mock import MagicMock

for _name in (
    "google",
    "google.cloud",
    "google.cloud.bigquery",
    "google.api_core",
    "google.protobuf",
):
    sys.modules.setdefault(_name, MagicMock())

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, StructureEvent
from orderflow_engine import metrics_processor as mp

mp.MSI_ENGINE_ENABLED = True
from orderflow_engine.metrics_processor import OrderFlowMetrics

MINUTE_MS = 60_000
# Okno zbliżone do produkcji: bootstrap kończy się 2026-09-28 11:22, live kilka godzin.
LIVE_END = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
BOOTSTRAP_LAST = datetime(2026, 9, 28, 11, 22, tzinfo=timezone.utc)
BOOTSTRAP_N = 280


def _fetch_m1(symbol: str, start_ms: int, end_ms: int) -> List[dict]:
    by_ts: Dict[int, dict] = {}
    cursor = end_ms
    pages = 0
    while True:
        params = urllib.parse.urlencode(
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "end": str(cursor),
                "limit": "1000",
            }
        )
        url = f"https://api.bybit.com/v5/market/kline?{params}"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "msi-kline-id/1.0"}),
            timeout=30,
        ) as r:
            payload = json.loads(r.read().decode())
        if payload.get("retCode") != 0:
            raise RuntimeError(payload)
        raw = (payload.get("result") or {}).get("list") or []
        pages += 1
        if not raw:
            break
        oldest = None
        for k in raw:
            ts = int(k[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts <= end_ms:
                by_ts[ts] = {
                    "ts": ts,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
        if oldest is None or oldest <= start_ms or len(raw) < 1000:
            break
        cursor = oldest - MINUTE_MS
        time.sleep(0.25)
        if pages > 20:
            break
    return [by_ts[t] for t in sorted(by_ts)]


def _event_key(e: StructureEvent) -> Tuple:
    payload = e.payload or {}
    mode = payload.get("mode")
    return (e.event_type, int(e.ts), mode, payload.get("ob_direction") or payload.get("direction"))


def _run_rest_path(symbol: str, bootstrap: List[dict], live: List[dict]) -> List[Tuple]:
    events: List[StructureEvent] = []
    eng = MsiEngine(symbol, on_event=lambda ev, _ob: events.append(ev))
    eng.set_replay_mode(True)
    for c in bootstrap:
        eng.on_candle_close(MsiCandle.from_dict(c))
    eng.set_replay_mode(False)
    for c in live:
        eng.on_candle_close(MsiCandle.from_dict(c))
    return [_event_key(e) for e in events if e.event_type in ("OB_NEW", "CHOCH")]


async def _run_kline_ws_path(symbol: str, bootstrap: List[dict], live: List[dict]) -> List[Tuple]:
    """Ścieżka jak produkcja po wdrożeniu: bootstrap REST + live przez process_closed_kline_1m."""
    events: List[StructureEvent] = []
    proc = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
    eng = MsiEngine(symbol, on_event=lambda ev, _ob: events.append(ev))
    proc.msi_engines[str(symbol).upper()] = eng
    proc.bootstrap_msi_structure(symbol, bootstrap, [])
    for c in live:
        await proc.process_closed_kline_1m(symbol, c, source="ws")
    return [_event_key(e) for e in events if e.event_type in ("OB_NEW", "CHOCH")]


def _compare(symbol: str, a: List[Tuple], b: List[Tuple]) -> None:
    ca = Counter(x[0] for x in a)
    cb = Counter(x[0] for x in b)
    print(f"=== {symbol} ===")
    print(f"  REST   OB_NEW={ca.get('OB_NEW',0)} CHOCH={ca.get('CHOCH',0)} n={len(a)}")
    print(f"  KLINE  OB_NEW={cb.get('OB_NEW',0)} CHOCH={cb.get('CHOCH',0)} n={len(b)}")
    if a != b:
        # show first mismatch
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(f"  MISMATCH at i={i}: REST={x} KLINE={y}")
                break
        else:
            print(f"  length differ REST={len(a)} KLINE={len(b)}")
        raise AssertionError(f"{symbol} event lists differ")
    print(f"  OK identical OB_NEW+CHOCH sequence")


async def _main_async() -> None:
    end_ms = int(LIVE_END.timestamp() * 1000)
    boot_last_ms = int(BOOTSTRAP_LAST.timestamp() * 1000)
    start_ms = boot_last_ms - (BOOTSTRAP_N - 1) * MINUTE_MS

    for symbol in ("BTCUSDT", "DOGEUSDT"):
        print(f"Fetching {symbol} M1 {BOOTSTRAP_N} bootstrap + live -> {LIVE_END.isoformat()} ...")
        rows = _fetch_m1(symbol, start_ms, end_ms)
        bootstrap = [r for r in rows if r["ts"] <= boot_last_ms]
        if len(bootstrap) > BOOTSTRAP_N:
            bootstrap = bootstrap[-BOOTSTRAP_N:]
        live = [r for r in rows if r["ts"] > boot_last_ms]
        assert len(bootstrap) == BOOTSTRAP_N, (symbol, len(bootstrap))
        assert live, symbol
        print(f"  bootstrap={len(bootstrap)} live={len(live)}")

        rest_keys = _run_rest_path(symbol, bootstrap, live)
        kline_keys = await _run_kline_ws_path(symbol, bootstrap, live)
        _compare(symbol, rest_keys, kline_keys)

    print("ALL integration MSI kline identity PASSED")


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
