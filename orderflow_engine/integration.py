# orderflow_engine/integration.py

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from orderflow_engine.metrics_processor import OrderFlowMetrics

logger = logging.getLogger(__name__)


class SignalContextBuilder:
    """
    Buduje pełny kontekst sygnału dla bot_service:
    - price, funding, OI
    - liquidity (likwidacje)
    - delta divergence history
    - DOM (OBI, walls)
    - structure (swingi)
    """

    def __init__(self, metrics: OrderFlowMetrics):
        self.metrics = metrics

    def build_context(self, symbol: str) -> Dict[str, Any]:
        """
        Zwraca gotowy kontekst w formacie JSON-owalnym.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # --- Podstawowe dane z metrics_processor ---
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