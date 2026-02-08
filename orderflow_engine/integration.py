# orderflow_engine/integration.py
import os
import time
import asyncio
import logging
import uuid
from datetime import datetime
from typing import List

import aiohttp

from .signal_detector import (
    SignalContext, SwingPoint, LiquidationEvent, DeltaPoint, DomSnapshot,
    should_generate_alert, build_alert_payload
)

# Importy z Twoich modułów (dostosuj ścieżki jeśli trzeba)
from . import market_structure
from . import metrics_processor

logger = logging.getLogger(__name__)
BOT_URL = os.getenv("BOT_SERVICE_URL", "https://trading-bot-service-785819958951.europe-central2.run.app")

# Per-symbol cooldown and duplicate guard
_symbol_last_alert_ts = {}
ALERT_COOLDOWN_SEC = 60


# -------------------------
# Helper: build SignalContext
# -------------------------
def build_context_from_sources(symbol: str,
                               price: float,
                               funding_rate: float) -> SignalContext:
    """
    Pobiera dane z istniejących modułów metrics_processor i market_structure
    i buduje SignalContext wymagany przez signal_detector.
    """
    # 1) Swing point (market_structure)
    swing = market_structure.get_latest_swing(symbol)  # powinno zwracać SwingPoint
    if swing is None:
        # brak swing pointa → nie budujemy kontekstu
        raise RuntimeError("No swing point available")

    # 2) Liquidations (metrics_processor)
    liquidations_raw = metrics_processor.get_recent_liquidations(symbol, window_sec=120)
    liquidations = [
        LiquidationEvent(side=l["side"], volume_usd=l["volume_usd"], timestamp=l["timestamp"])
        for l in liquidations_raw
    ]

    # 3) Deltas
    deltas_raw = metrics_processor.get_recent_deltas(symbol, limit=10)
    deltas = [DeltaPoint(price=d["price"], delta=d["delta"], timestamp=d["timestamp"]) for d in deltas_raw]

    # 4) DOM snapshot
    dom_raw = metrics_processor.get_dom_snapshot(symbol)
    dom = DomSnapshot(bids=dom_raw["bids"], asks=dom_raw["asks"], obi=dom_raw["obi"])

    # 5) Direction: ask market_structure for sweep direction (LONG/SHORT/None)
    direction = market_structure.get_sweep_direction(symbol, price)
    if direction not in ("LONG", "SHORT"):
        raise RuntimeError("No sweep direction")

    ctx = SignalContext(
        direction=direction,
        current_price=price,
        swing_point=swing,
        liquidations=liquidations,
        recent_deltas=deltas,
        dom_snapshot=dom,
        funding_rate=funding_rate
    )
    return ctx


# -------------------------
# Helper: send alert to bot_service
# -------------------------
async def send_alert_to_bot(alert: dict):
    url = BOT_URL.rstrip("/") + "/process-alerts"
    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=alert) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.error("Alert send failed status=%s body=%s", resp.status, text)
                    return False
                logger.info("Alert sent OK event_id=%s", alert.get("event_id"))
                return True
    except Exception as e:
        logger.exception("Exception while sending alert: %s", e)
        return False


# -------------------------
# Main integration function called from websocket_handler
# -------------------------
async def process_tick_and_maybe_alert(symbol: str,
                                       price: float,
                                       tick_size: float,
                                       funding_rate: float):
    """
    Wywoływane z websocket_handler przy każdym ticku / update orderbook.
    - Buduje kontekst
    - Wywołuje should_generate_alert
    - Jeśli True -> buduje payload i wysyła do bot_service
    - Zabezpieczenia: cooldown per symbol, duplicate guard
    """
    now = time.time()
    last_ts = _symbol_last_alert_ts.get(symbol, 0)
    if now - last_ts < ALERT_COOLDOWN_SEC:
        # cooldown
        return

    try:
        ctx = build_context_from_sources(symbol, price, funding_rate)
    except Exception as e:
        # brak danych strukturalnych → nic nie robimy
        logger.debug("Context build failed for %s: %s", symbol, e)
        return

    # Decision
    try:
        should = should_generate_alert(ctx)
    except Exception as e:
        logger.exception("Error in decision logic: %s", e)
        return

    if not should:
        logger.debug("No alert for %s at price %s", symbol, price)
        return

    # Build entry/SL/TP using DOM wall price
    # Prefer the strongest wall price from top of DOM snapshot
    if ctx.direction == "LONG":
        wall_price = ctx.dom_snapshot.bids[0][0] if ctx.dom_snapshot.bids else price
        entry = wall_price + tick_size
        sl = entry * (1 - 0.006)
        tp = entry * (1 + 0.018)
    else:
        wall_price = ctx.dom_snapshot.asks[0][0] if ctx.dom_snapshot.asks else price
        entry = wall_price - tick_size
        sl = entry * (1 + 0.006)
        tp = entry * (1 - 0.018)

    alert = build_alert_payload(symbol, ctx.direction, entry, sl, tp, ctx)

    # Mark last alert timestamp immediately to avoid duplicates
    _symbol_last_alert_ts[symbol] = now

    # Send alert asynchronously
    sent = await send_alert_to_bot(alert)
    if not sent:
        # if failed, allow retry after short backoff by resetting timestamp slightly earlier
        _symbol_last_alert_ts[symbol] = now - (ALERT_COOLDOWN_SEC * 0.5)


# -------------------------
# Synchronous wrapper for non-async websocket loops
# -------------------------
def process_tick_sync(symbol: str, price: float, tick_size: float, funding_rate: float):
    """
    If websocket handler is synchronous, call this helper to schedule async task.
    """
    loop = None
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(process_tick_and_maybe_alert(symbol, price, tick_size, funding_rate), loop)
    else:
        asyncio.run(process_tick_and_maybe_alert(symbol, price, tick_size, funding_rate))