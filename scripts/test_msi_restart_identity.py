#!/usr/bin/env python3
"""
Deterministyczny test tożsamości MSI przy restartach (bez Firestore / Bybit).

A) 20 000 świec ciągiem
B) te same świece, 5 restartów: export_state → nowy MsiEngine → import_state → dalsze świece

Listy zdarzeń (typ, ts, poziomy, chain_id) muszą być identyczne.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, OrderBlock, StructureEvent

N_CANDLES = 20_000
RNG_SEED = 42
N_RESTARTS = 5
START_TS = 1_700_000_000_000


def generate_candles(n: int = N_CANDLES) -> list[MsiCandle]:
    rng = random.Random(1)
    candles: list[MsiCandle] = []
    price = 100.0
    for i in range(n):
        phase = i % 80
        if phase < 40:
            delta = 0.15
        elif phase < 60:
            delta = -0.12
        else:
            delta = 0.08
        noise = rng.uniform(-0.02, 0.02)
        o = price
        price = max(50.0, price + delta + noise)
        c = price
        hi = max(o, c) + abs(rng.uniform(0.01, 0.08))
        lo = min(o, c) - abs(rng.uniform(0.01, 0.08))
        candles.append(
            MsiCandle(
                ts=START_TS + i * 60_000,
                open=o,
                high=hi,
                low=lo,
                close=c,
                volume=1.0,
            )
        )
    return candles


def event_record(ev: StructureEvent) -> tuple:
    payload = ev.payload or {}
    return (
        ev.event_type,
        int(ev.ts),
        str(ev.symbol),
        str(payload.get("chain_id") or payload.get("ob_chain_id") or ""),
        json.dumps(payload, sort_keys=True, default=str),
    )


def collect_events(candles: list[MsiCandle], *, restart_at: list[int] | None) -> list[tuple]:
    records: list[tuple] = []

    def sink(ev: StructureEvent, _ob: OrderBlock | None) -> None:
        records.append(event_record(ev))

    engine = MsiEngine("TESTUSDT", on_event=sink)
    cuts = list(restart_at or [])
    bounds = [0] + cuts + [len(candles)]
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        for c in candles[lo:hi]:
            engine.on_candle_close(c)
        if hi < len(candles):
            snap = engine.export_state()
            engine = MsiEngine("TESTUSDT", on_event=sink)
            engine.import_state(snap)
    return records


def pick_restart_indices(n: int, k: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    lo = max(200, n // 20)
    hi = n - max(200, n // 20)
    return sorted(rng.sample(range(lo, hi), k))


def main() -> None:
    candles = generate_candles(N_CANDLES)
    restarts = pick_restart_indices(N_CANDLES, N_RESTARTS, RNG_SEED)
    print("=== MSI RESTART IDENTITY TEST ===")
    print(f"candles={len(candles)} restarts_at_index={restarts}")

    events_a = collect_events(candles, restart_at=None)
    events_b = collect_events(candles, restart_at=restarts)

    n_ob_a = sum(1 for e in events_a if e[0] == "OB_NEW")
    n_ob_b = sum(1 for e in events_b if e[0] == "OB_NEW")
    print(f"A events={len(events_a)} OB_NEW={n_ob_a}")
    print(f"B events={len(events_b)} OB_NEW={n_ob_b}")

    if events_a == events_b:
        print("RESULT: PASS - A i B identyczne (typ, ts, poziomy, chain_id)")
        return

    print("RESULT: FAIL")
    print(f"len A={len(events_a)} len B={len(events_b)}")
    limit = min(len(events_a), len(events_b))
    diffs = 0
    for i in range(limit):
        if events_a[i] != events_b[i]:
            print(f"  first diff i={i}")
            print(f"    A={events_a[i][:4]}")
            print(f"    B={events_b[i][:4]}")
            diffs += 1
            if diffs >= 8:
                break
    if len(events_a) != len(events_b):
        print("  length mismatch")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
