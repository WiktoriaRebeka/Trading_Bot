#!/usr/bin/env python3
"""
Smoke test: buduje realistyczny sygnał, waliduje AlertData (market_features),
wstawia wiersz do market_structure_signals i wypisuje pełny rekord.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared_lib.models import AlertData
from orderflow_engine.metrics_processor import OrderFlowMetrics, DOMSnapshot, OrderBookWall
from orderflow_engine.integration import SignalContextBuilder, _build_market_features
from orderflow_engine.risk_levels import calculate_structure_risk_levels
from orderflow_engine.signal_detector import SignalContext, SwingPoint, DeltaPoint, DomSnapshot
from bot_service.bigquery_logger import (
    build_market_structure_signal_row,
    initialize_bigquery,
    get_bigquery_client,
)


def _seed_processor(symbol: str = "BTCUSDT") -> OrderFlowMetrics:
    p = OrderFlowMetrics()
    sym = symbol.upper()
    now_ms = int(time.time() * 1000)

    p.tickers[sym] = {
        "price": 95000.0,
        "funding_rate": 0.00012,
        "open_interest": 123456789.0,
        "volume_24h": 9876543210.0,
    }

    bids = [(94999.0, 2.5), (94998.0, 3.0), (94997.0, 1.2), (94996.0, 0.8)] + [(94995.0 - i, 0.5) for i in range(6)]
    asks = [(95001.0, 1.0), (95002.0, 0.9), (95003.0, 4.5), (95004.0, 0.7)] + [(95005.0 + i, 0.4) for i in range(6)]
    bid_walls = [OrderBookWall(94997.0, 4.5, 2.0, "bid")]
    ask_walls = [OrderBookWall(95003.0, 4.5, 2.0, "ask")]
    p.orderbook_snapshots[sym] = DOMSnapshot(
        symbol=sym,
        bids=bids,
        asks=asks,
        timestamp=now_ms,
        obi=0.15,
        best_bid=94999.0,
        best_ask=95001.0,
        bid_walls=bid_walls,
        ask_walls=ask_walls,
    )

    engine = p.engines[sym]
    engine.last_swing_low = 95100.0
    engine.last_swing_high = 96000.0
    for i in range(30):
        engine.candles.append({"open": 95100, "high": 95150, "low": 95080 + (i % 3), "close": 95100, "ts": now_ms - i * 60000})

    for i in range(15):
        ts = now_ms - (14 - i) * 20000
        side = "Buy" if i % 2 == 0 else "Sell"
        p.trades[sym].append({"timestamp": ts, "side": side, "qty": 0.01, "price": 94950.0 - i * 2})

    p.delta_history[sym] = deque(maxlen=200)
    for i in range(35):
        ts = now_ms - (34 - i) * 10000
        delta = -5000.0 + i * 300.0
        p.delta_history[sym].append({"price": 94950.0 - i, "delta": delta, "timestamp": ts})

    from orderflow_engine.metrics_processor import LiquidationEvent
    p.liquidations[sym] = [
        LiquidationEvent(sym, "Buy", 94900.0, 1.0, now_ms - 60000, 15000.0),
        LiquidationEvent(sym, "Buy", 94880.0, 0.5, now_ms - 120000, 8000.0),
    ]

    return p


def main() -> int:
    sym = "BTCUSDT"
    processor = _seed_processor(sym)
    direction = "LONG"
    builder = SignalContextBuilder(processor)
    ctx = builder.build_signal_context(sym, direction)
    if ctx is None:
        print("FAIL: build_signal_context returned None")
        return 1

    div_result = processor._detect_delta_divergence(sym)
    entry = ctx.current_price
    risk_levels = calculate_structure_risk_levels(sym, direction, entry, processor.engines[sym], 75.0, __import__("logging").getLogger("smoke"))
    if risk_levels is None:
        print("FAIL: risk_levels is None")
        return 1

    market_features = _build_market_features(ctx, processor, risk_levels, div_result)
    _now = datetime.now(timezone.utc)

    alert_payload = {
        "event_id": f"SMOKE-{sym}-{int(time.time())}",
        "signal_id": f"AUTO-SMOKE-{sym}",
        "symbol": sym,
        "timestamp": _now.isoformat().replace("+00:00", "Z"),
        "direction": direction,
        "entry": entry,
        "sl": risk_levels.sl,
        "tp": risk_levels.tp,
        "risk_pct": risk_levels.risk_pct,
        "rr": risk_levels.rr,
        "risk_usdt": 2.5,
        "structure_state": 1,
        "session": "LONDON",
        "minute_of_day": _now.hour * 60 + _now.minute,
        "day_of_week": _now.weekday(),
        "market_features": market_features,
        "raw_context": {"smoke_test": True},
    }

    signal = AlertData.model_validate(alert_payload)
    assert signal.market_features is not None, "market_features lost after Pydantic validation"
    assert signal.market_features.get("matched_liq_volume") is not None
    print("PYDANTIC OK: market_features preserved")
    print(f"  keys={len(signal.market_features)} non_null={sum(1 for v in signal.market_features.values() if v is not None)}")
    print(f"  matched_liq_volume={signal.market_features.get('matched_liq_volume')}")
    print(f"  funding_rate={signal.market_features.get('funding_rate')}")
    print(f"  real_wall_detected={signal.market_features.get('real_wall_detected')}")

    f_entry = 94999.0
    f_sl = risk_levels.sl
    planned_2r = f_entry + 2 * abs(f_entry - f_sl)
    sl_distance = abs(f_entry - f_sl)
    qty = 0.001
    analysis_data = {
        **{k: alert_payload[k] for k in ("event_id", "signal_id", "symbol", "timestamp", "direction", "session", "minute_of_day", "day_of_week", "structure_state")},
        "entry": f_entry,
        "sl": f_sl,
        "tp": planned_2r,
        "rr": (planned_2r - f_entry) / sl_distance,
        "risk_pct": sl_distance / f_entry * 100.0,
        "risk_usdt": qty * sl_distance,
        "market_features": signal.market_features,
        "raw_context": alert_payload["raw_context"],
    }

    row = build_market_structure_signal_row(analysis_data)
    print("\n=== FULL ROW (market_structure_signals) ===")
    print(json.dumps(row, indent=2, default=str))

    null_fields = [k for k, v in row.items() if v is None]
    non_null = [k for k, v in row.items() if v is not None]
    print(f"\nNon-null fields ({len(non_null)}): {non_null}")
    print(f"NULL fields ({len(null_fields)}): {null_fields}")

    if not initialize_bigquery():
        print("\nBQ insert skipped — brak połączenia z BigQuery (wiersz zbudowany lokalnie).")
        return 0

    client = get_bigquery_client()
    table_ref = client.dataset("trading_analytics").table("market_structure_signals")
    errors = client.insert_rows_json(table_ref, [row])
    if errors:
        print(f"\nBQ INSERT ERRORS: {errors}")
        return 1

    print(f"\nBQ INSERT OK event_id={row['event_id']}")
    query = f"""
        SELECT *
        FROM `trading_analytics.market_structure_signals`
        WHERE event_id = @event_id
        ORDER BY timestamp DESC
        LIMIT 1
    """
    job = client.query(
        query,
        job_config=__import__("google.cloud.bigquery", fromlist=["bigquery"]).bigquery.QueryJobConfig(
            query_parameters=[
                __import__("google.cloud.bigquery", fromlist=["bigquery"]).bigquery.ScalarQueryParameter(
                    "event_id", "STRING", row["event_id"]
                )
            ]
        ),
    )
    results = list(job.result())
    if results:
        print("\n=== BQ QUERY BACK ===")
        print(json.dumps(dict(results[0].items()), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
