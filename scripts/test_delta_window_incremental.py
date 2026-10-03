#!/usr/bin/env python3
"""Identity + timing: brute-force scan vs incremental 300s delta."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, patch

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
    "google.api_core",
    "google.protobuf",
):
    sys.modules.setdefault(_name, MagicMock())

from orderflow_engine import metrics_processor as mp

mp.MSI_ENGINE_ENABLED = True
from orderflow_engine.metrics_processor import OrderFlowMetrics


def _gen_trades(
    n: int, span_sec: float, *, now_ms: int
) -> List[Tuple[int, str, float, float]]:
    start_ms = now_ms - int(span_sec * 1000)
    out = []
    for i in range(n):
        ts = start_ms + int(i * (span_sec * 1000) / max(n - 1, 1))
        side = "Buy" if i % 2 == 0 else "Sell"
        qty = 0.01 + (i % 17) * 0.001
        price = 65000.0 + (i % 50) * 0.5
        out.append((ts, side, qty, price))
    return out


def main() -> None:
    # Zamroź wall-clock na czas całego testu — cutoff identyczny dla scan i incr.
    frozen_s = time.time()
    now_ms = int(frozen_s * 1000)
    trades = _gen_trades(25000, 500.0, now_ms=now_ms)

    old_p = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)
    new_p = OrderFlowMetrics(firestore_client=None, vp_fetcher=None)

    t_old = 0.0
    t_new = 0.0
    n = len(trades)

    with patch.object(mp.time, "time", return_value=frozen_s):
        for i, (ts, side, qty, price) in enumerate(trades):
            t0 = time.perf_counter()
            s = "Buy" if str(side).lower() == "buy" else "Sell"
            old_p.trades["BTCUSDT"].append(
                {"timestamp": int(ts), "side": s, "qty": float(qty), "price": float(price)}
            )
            d1, b1, s1 = old_p._calculate_delta_window_volumes_scan("BTCUSDT", 300)
            t_old += time.perf_counter() - t0

            t0 = time.perf_counter()
            new_p.process_trade(ts, "BTCUSDT", side, qty, price)
            fw = new_p.flow_window_300s["BTCUSDT"]
            d2, b2, s2 = fw["buy"] - fw["sell"], fw["buy"], fw["sell"]
            t_new += time.perf_counter() - t0

            if abs(d1 - d2) > 1e-4 or abs(b1 - b2) > 1e-4 or abs(s1 - s2) > 1e-4:
                print(f"MISMATCH i={i} old=({d1},{b1},{s1}) new=({d2},{b2},{s2})")
                print(
                    f"  scan_on_new={new_p._calculate_delta_window_volumes_scan('BTCUSDT', 300)} "
                    f"q_len={len(new_p._flow300_q['BTCUSDT'])} "
                    f"deque={len(new_p.trades['BTCUSDT'])}"
                )
                raise SystemExit(1)

    print(f"IDENTITY OK ticks={n} (frozen wall-clock)")
    print(f"TIME old(scan append+scan)/tick: {t_old / n * 1e6:.2f} us")
    print(f"TIME new(process_trade incr)/tick: {t_new / n * 1e6:.2f} us")
    print(f"SPEEDUP: {t_old / max(t_new, 1e-12):.1f}x")
    print(
        f"deque={len(new_p.trades['BTCUSDT'])} "
        f"window_q={len(new_p._flow300_q['BTCUSDT'])} "
        f"flow={new_p.flow_window_300s['BTCUSDT']}"
    )

    # Steady-state: pełny deque, żywy zegar (jak produkcja)
    n2 = 500
    live_now = int(time.time() * 1000)
    t0 = time.perf_counter()
    for j in range(n2):
        new_p.process_trade(live_now + j, "BTCUSDT", "Buy", 0.01, 65000.0)
    print(f"STEADY process_trade us/call: {(time.perf_counter() - t0) / n2 * 1e6:.2f}")
    print("PRE-FIX bench (full deque): scan ~2669 us/call, process_trade ~2734 us/call")


if __name__ == "__main__":
    main()
