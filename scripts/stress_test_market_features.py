#!/usr/bin/env python3
"""Stress-test _build_market_features on empty/partial live-like buffers."""
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.integration import _build_market_features
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.risk_levels import StructureRiskLevels
from orderflow_engine.signal_detector import SignalContext, SwingPoint, DeltaPoint, DomSnapshot


def _minimal_risk():
    return StructureRiskLevels(
        sl=100.0, tp=110.0, risk_pct=1.0, rr=2.0,
        swing_level=99.0, fallback_used=False, tp_capped=False,
    )


def _ctx(sym, direction, price=100.0, swing=99.0, obi=0.0, liqs=None, deltas=None):
    return SignalContext(
        symbol=sym,
        direction=direction,
        current_price=price,
        swing_point=SwingPoint(price=swing, timestamp=datetime.now(timezone.utc)),
        liquidations=liqs or [],
        recent_deltas=deltas or [],
        dom_snapshot=DomSnapshot(bids=[], asks=[], obi=obi),
        funding_rate=0.0,
    )


def _run_case(name, processor, ctx, div_result):
    try:
        mf = _build_market_features(ctx, processor, _minimal_risk(), div_result)
        nn = sum(1 for v in mf.values() if v is not None)
        print(f"OK  {name}: non_null={nn}/{len(mf)}")
        return True
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main():
    sym = "BTCUSDT"
    p = OrderFlowMetrics()
    div = {"detected": False}

    cases = [
        ("empty_everything", _ctx(sym, "LONG"), p),
        ("empty_short", _ctx(sym, "SHORT", price=101, swing=102), p),
        ("no_dom_snapshot", _ctx(sym, "LONG"), p),
        ("zero_swing", _ctx(sym, "LONG", swing=0.0), p),
        ("nan_oi_ticker", _ctx(sym, "LONG"), p),
    ]
    p.tickers[sym] = {"price": 100, "funding_rate": 0, "open_interest": float("nan"), "volume_24h": 0}

    # partial dom only
    p2 = OrderFlowMetrics()
    p2.orderbook_snapshots[sym] = None  # force empty via get_dom_snapshot
    cases.append(("no_orderbook", _ctx(sym, "LONG"), p2))

    p3 = OrderFlowMetrics()
    p3.tickers[sym] = {"price": 100.0}
    p3.engines[sym].last_swing_low = 99.0
    cases.append(("minimal_long", _ctx(sym, "LONG", price=98.5, swing=99.0, obi=0.2), p3))

    ok = 0
    for name, ctx, proc in cases:
        if _run_case(name, proc, ctx, div):
            ok += 1

    # walls as raw OrderBookWall objects in snapshot (regression)
    from orderflow_engine.metrics_processor import DOMSnapshot, OrderBookWall
    p4 = OrderFlowMetrics()
    p4.orderbook_snapshots[sym] = DOMSnapshot(
        symbol=sym, bids=[(99.0, 1.0)], asks=[(101.0, 1.0)],
        timestamp=int(time.time() * 1000), obi=0.2,
        best_bid=99.0, best_ask=101.0,
        bid_walls=[OrderBookWall(99.0, 5.0, 1.0, "bid")],
        ask_walls=[],
    )
    if _run_case("real_wall_objects", _ctx(sym, "LONG", price=98.5, swing=99.0, obi=0.2), p4, div):
        ok += 1

    print(f"\nPassed {ok}/{len(cases)+1}")
    return 0 if ok == len(cases) + 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
