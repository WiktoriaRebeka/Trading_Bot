#!/usr/bin/env python3
"""
Tożsamość MSI: ścieżka kline + 3 symulowane restarty vs ciąg bez restartów.

A) bootstrap + live ciągle przez process_closed_kline_1m
B) te same świece, 3× (export_state → nowy silnik → import → gap-fill REST-like
   w replay_mode → mark_bootstrap_done → dalsze kline)

Porównanie: sekwencje OB_NEW i CHOCH muszą być identyczne (BTCUSDT, DOGEUSDT).
Prawdziwe świece Bybit M1, kilka dni.
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
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SIGNAL_MODE", "msi_orderblock")
os.environ["MSI_ENGINE_ENABLED"] = "true"
os.environ.setdefault("MSI_TRADE_ENABLED", "false")
os.environ.setdefault("MSI_LOG_TO_BQ", "false")

for _name in (
    "google",
    "google.cloud",
    "google.cloud.bigquery",
    "google.cloud.firestore",
    "google.api_core",
    "google.protobuf",
):
    sys.modules.setdefault(_name, MagicMock())

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, StructureEvent
from orderflow_engine import metrics_processor as mp

mp.MSI_ENGINE_ENABLED = True
from orderflow_engine.metrics_processor import OrderFlowMetrics

MINUTE_MS = 60_000
# ~3 dni live po bootstrapie 280 M1
LIVE_END = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
BOOTSTRAP_LAST = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
BOOTSTRAP_N = 280
N_RESTARTS = 3


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
            urllib.request.Request(url, headers={"User-Agent": "msi-kline-restart/1.0"}),
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
        time.sleep(0.2)
        if pages > 40:
            break
    return [by_ts[t] for t in sorted(by_ts)]


def _event_key(e: StructureEvent) -> Tuple:
    payload = e.payload or {}
    return (
        e.event_type,
        int(e.ts),
        payload.get("mode"),
        payload.get("ob_direction") or payload.get("direction"),
        payload.get("chain_id") or payload.get("ob_chain_id") or "",
    )


def _pick_restart_cuts(n_live: int, k: int = N_RESTARTS) -> List[int]:
    """Indeksy w live[] po których robimy restart (po zamknięciu świecy)."""
    if n_live < 200:
        raise RuntimeError(f"live too short for restarts: {n_live}")
    # Równomiernie w środkowych 80% okna
    lo = max(50, n_live // 10)
    hi = n_live - max(50, n_live // 10)
    span = hi - lo
    return [lo + (span * (i + 1)) // (k + 1) for i in range(k)]


async def _run_continuous(
    symbol: str, bootstrap: List[dict], live: List[dict]
) -> List[Tuple]:
    events: List[StructureEvent] = []
    proc = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
    eng = MsiEngine(symbol, on_event=lambda ev, _ob: events.append(ev))
    proc.msi_engines[str(symbol).upper()] = eng
    # bootstrap: replay ON w bootstrap_msi_structure; potem mark_bootstrap_done
    proc.bootstrap_msi_structure(symbol, bootstrap, [])
    for c in live:
        await proc.process_closed_kline_1m(symbol, c, source="ws")
    return [_event_key(e) for e in events if e.event_type in ("OB_NEW", "CHOCH")]


async def _run_with_restarts(
    symbol: str,
    bootstrap: List[dict],
    live: List[dict],
    restart_cuts: List[int],
) -> List[Tuple]:
    """
    Symulacja produkcji przy restarcie:
      export_state → nowy MsiEngine → import → gap-fill w replay_mode
      → mark_bootstrap_done(last_fed) → dalsze kline (ws).
    """
    events: List[StructureEvent] = []
    sym = str(symbol).upper()
    cut_set = set(restart_cuts)

    def sink(ev: StructureEvent, _ob) -> None:
        events.append(ev)

    proc = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
    eng = MsiEngine(sym, on_event=sink)
    proc.msi_engines[sym] = eng
    proc.bootstrap_msi_structure(sym, bootstrap, [])

    i = 0
    while i < len(live):
        c = live[i]
        await proc.process_closed_kline_1m(sym, c, source="ws")
        if i in cut_set and i + 1 < len(live):
            # --- restart boundary (jak Cloud Run OOM/redeploy) ---
            snap = eng.export_state()
            last_ts = int(snap.get("last_processed_ts") or c["ts"])
            # Nowa instancja procesora + silnika (jak po restarcie kontenera)
            proc = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
            eng = MsiEngine(sym, on_event=sink)
            proc.msi_engines[sym] = eng
            eng.set_replay_mode(True)
            eng.import_state(snap)
            # Gap-fill: świece live po last_ts aż do „teraz” = następna świeca przed i+1
            # (symulujemy resume tuż przed live[i+1]; dogrywamy brakujące w replay)
            gap_end_exclusive = int(live[i + 1]["ts"])
            fed = 0
            last_fed = last_ts
            j = i + 1
            while j < len(live) and int(live[j]["ts"]) < gap_end_exclusive:
                # nie powinno wejść przy cut zaraz przed i+1
                proc._feed_msi_candle(sym, live[j])
                last_fed = int(live[j]["ts"])
                fed += 1
                j += 1
            # Typowy przypadek: last_ts == live[i].ts, brak luki → fed=0
            eng.set_replay_mode(False)
            proc._msi_kline.mark_bootstrap_done(sym, int(last_fed))
            # Kontynuacja od live[i+1] jak WS po resume
            i += 1
            continue
        i += 1

    return [_event_key(e) for e in events if e.event_type in ("OB_NEW", "CHOCH")]


def _compare(symbol: str, a: List[Tuple], b: List[Tuple]) -> None:
    ca = Counter(x[0] for x in a)
    cb = Counter(x[0] for x in b)
    print(f"=== {symbol} ===")
    print(f"  CONT   OB_NEW={ca.get('OB_NEW', 0)} CHOCH={ca.get('CHOCH', 0)} n={len(a)}")
    print(f"  RESTART OB_NEW={cb.get('OB_NEW', 0)} CHOCH={cb.get('CHOCH', 0)} n={len(b)}")
    if a != b:
        for idx, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(f"  MISMATCH at i={idx}: CONT={x} RESTART={y}")
                break
        else:
            print(f"  length differ CONT={len(a)} RESTART={len(b)}")
        raise AssertionError(f"{symbol} OB_NEW/CHOCH sequences differ")
    print("  OK identical OB_NEW+CHOCH")


async def _main_async() -> None:
    end_ms = int(LIVE_END.timestamp() * 1000)
    boot_last_ms = int(BOOTSTRAP_LAST.timestamp() * 1000)
    start_ms = boot_last_ms - (BOOTSTRAP_N - 1) * MINUTE_MS

    print("=== MSI KLINE + 3 RESTARTS IDENTITY ===")
    print(f"bootstrap_last={BOOTSTRAP_LAST.isoformat()} live_end={LIVE_END.isoformat()}")

    for symbol in ("BTCUSDT", "DOGEUSDT"):
        print(f"Fetching {symbol} M1 ...")
        rows = _fetch_m1(symbol, start_ms, end_ms)
        bootstrap = [r for r in rows if r["ts"] <= boot_last_ms]
        if len(bootstrap) > BOOTSTRAP_N:
            bootstrap = bootstrap[-BOOTSTRAP_N:]
        live = [r for r in rows if r["ts"] > boot_last_ms]
        assert len(bootstrap) == BOOTSTRAP_N, (symbol, len(bootstrap))
        assert len(live) > 500, (symbol, len(live))
        cuts = _pick_restart_cuts(len(live), N_RESTARTS)
        print(f"  bootstrap={len(bootstrap)} live={len(live)} restarts_at_live_idx={cuts}")

        cont = await _run_continuous(symbol, bootstrap, live)
        rest = await _run_with_restarts(symbol, bootstrap, live, cuts)
        _compare(symbol, cont, rest)

    print("ALL kline+restart MSI identity PASSED")


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
