# orderflow_engine/integration.py

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any

from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.signal_detector import (
    detect_liquidity_sweep,
    check_liquidations,
    check_delta_divergence,
    check_dom_wall,
    compute_confidence_score
)
from orderflow_engine.bot_sender import send_alert_to_bot  # <-- poprawny import

logger = logging.getLogger(__name__)


class SignalContextBuilder:
    """
    Buduje pełny kontekst sygnału dla bot_service.
    """

    def __init__(self, metrics: OrderFlowMetrics):
        self.metrics = metrics

    def build_context(self, symbol: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        last_price = self.metrics.get_last_price(symbol)
        funding = self.metrics.get_last_funding(symbol)
        ticker = self.metrics.tickers.get(symbol, {})
        dom = self.metrics.get_dom_snapshot(symbol)
        liqs = self.metrics.get_recent_liquidations(symbol, window_sec=60)
        deltas = self.metrics.get_recent_deltas(symbol, limit=30)
        full_ctx = self.metrics.get_full_context(symbol)

        structure = full_ctx.get("structure", {})
        dom_ctx = full_ctx.get("dom", {})

        return {
            "symbol": symbol,
            "timestamp": now,
            "price": last_price,
            "funding_rate": funding,
            "open_interest": ticker.get("open_interest"),
            "volume_24h": ticker.get("volume_24h"),

            "structure": {
                "last_swing_high": structure.get("last_swing_high"),
                "last_swing_low": structure.get("last_swing_low"),
            },

            "dom": {
                "obi": dom_ctx.get("obi", 0.0),
                "bids": dom.get("bids", []),
                "asks": dom.get("asks", []),
            },

            "liquidations": liqs,
            "delta_points": deltas,
        }


async def evaluate_and_maybe_alert(symbol: str, processor: OrderFlowMetrics):
    """
    Główna funkcja decyzyjna — wywoływana po każdym ticku/orderbooku.
    """

    builder = SignalContextBuilder(processor)
    ctx = builder.build_context(symbol)

    if ctx is None:
        return

    # 1. Sweep
    if not detect_liquidity_sweep(ctx):
        return

    # 2. Liquidations
    if not check_liquidations(ctx):
        return

    # 3. Delta divergence
    if not check_delta_divergence(ctx):
        return

    # 4. DOM wall
    if not check_dom_wall(ctx):
        return

    # 5. Confidence score
    score = compute_confidence_score(ctx)
    if score < 70:
        return

    # 6. Build alert
    alert = {
        "event_id": f"{symbol}-{int(time.time())}",
        "signal_id": f"{symbol}-{ctx['timestamp']}",
        "symbol": symbol,
        "direction": ctx["structure"].get("direction", "LONG"),
        "entry": ctx["price"],
        "sl": ctx["price"] * 0.99,
        "tp": ctx["price"] * 1.03,
        "risk_pct": 1.0,
        "rr": 3.0,
        "risk_usdt": 50,
        "structure_state": 1,
        "raw_context": ctx
    }

    logger.info(f"[ALERT] Generated alert for {symbol}")
    await send_alert_to_bot(alert)