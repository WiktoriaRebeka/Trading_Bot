# orderflow_engine/integration.py
# WERSJA POPRAWIONA - Kompletny pipeline generowania alertów

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
    compute_confidence_score,
    SignalContext,
    SwingPoint,
    LiquidationEvent,
    DeltaPoint,
    DomSnapshot
)
from orderflow_engine.bot_sender import send_alert_to_bot

logger = logging.getLogger(__name__)


class SignalContextBuilder:
    """
    Buduje pełny kontekst sygnału dla signal_detector.
    """

    def __init__(self, metrics: OrderFlowMetrics):
        self.metrics = metrics

    def build_context(self, symbol: str) -> Dict[str, Any]:
        """
        Zwraca kontekst mikrostruktury dla bot_service (endpoint /context/{symbol})
        """
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

    def build_signal_context(self, symbol: str, direction: str) -> SignalContext:
        """
        Buduje SignalContext dla signal_detector.
        """
        engine = self.metrics.engines[symbol]
        current_price = self.metrics.get_last_price(symbol)
        
        # Swing point
        if direction == "LONG":
            swing_price = engine.last_swing_low or current_price
        else:
            swing_price = engine.last_swing_high or current_price
        
        swing_point = SwingPoint(
            price=swing_price,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Liquidations
        liqs_raw = self.metrics.get_recent_liquidations(symbol, window_sec=60)
        liquidations = [
            LiquidationEvent(
                side=l['side'],
                volume_usd=l['volume_usd'],
                timestamp=l['timestamp']
            )
            for l in liqs_raw
        ]
        
        # Delta points
        deltas_raw = self.metrics.get_recent_deltas(symbol, limit=30)
        recent_deltas = [
            DeltaPoint(
                price=d['price'],
                delta=d['delta'],
                timestamp=d['timestamp']
            )
            for d in deltas_raw
        ]
        
        # DOM
        dom_raw = self.metrics.get_dom_snapshot(symbol)
        dom_snapshot = DomSnapshot(
            bids=dom_raw.get('bids', []),
            asks=dom_raw.get('asks', []),
            obi=dom_raw.get('obi', 0.0)
        )
        
        funding_rate = self.metrics.get_last_funding(symbol)
        
        return SignalContext(
            direction=direction,
            current_price=current_price,
            swing_point=swing_point,
            liquidations=liquidations,
            recent_deltas=recent_deltas,
            dom_snapshot=dom_snapshot,
            funding_rate=funding_rate
        )


async def evaluate_and_maybe_alert(symbol: str, processor: OrderFlowMetrics):
    """
    Główna funkcja decyzyjna — wywoływana po każdym ticku/orderbooku.
    
    KOMPLETNY PIPELINE:
    1. Sprawdź warunki strukturalne (sweep)
    2. Sprawdź likwidacje (min 50k USD)
    3. Sprawdź delta divergence
    4. Sprawdź DOM wall + OBI
    5. Oblicz confidence score (min 70)
    6. Zbuduj alert payload
    7. Wyślij do bot_service
    """
    
    builder = SignalContextBuilder(processor)
    
    # Sprawdź obie strony (LONG i SHORT)
    for direction in ["LONG", "SHORT"]:
        try:
            # Build context
            ctx = builder.build_signal_context(symbol, direction)
            
            # 1. Sweep
            if not detect_liquidity_sweep(ctx):
                continue
            
            logger.debug(f"[{symbol}] Sweep detected for {direction}")
            
            # 2. Liquidations
            liq_ok = check_liquidations(ctx, min_volume_usd=50_000.0)
            if not liq_ok:
                logger.debug(f"[{symbol}] Liquidations check failed for {direction}")
                continue
            
            # 3. Delta divergence
            delta_ok = check_delta_divergence(ctx)
            if not delta_ok:
                logger.debug(f"[{symbol}] Delta divergence check failed for {direction}")
                continue
            
            # 4. DOM wall
            dom_ok = check_dom_wall(ctx)
            if not dom_ok:
                logger.debug(f"[{symbol}] DOM wall check failed for {direction}")
                continue
            
            # 5. Confidence score
            score = compute_confidence_score(
                ctx,
                liq_ok=True,
                delta_ok=True,
                dom_ok=True,
                structure_quality=1.0  # TODO: get from MarketStructureEngine
            )
            
            if score < 70:
                logger.debug(f"[{symbol}] Confidence score too low: {score:.1f}/100")
                continue
            
            # 6. Build alert
            entry = ctx.current_price
            if direction == "LONG":
                sl = entry * 0.994  # -0.6%
                tp = entry * 1.018  # +1.8% (RR=3.0)
            else:
                sl = entry * 1.006  # +0.6%
                tp = entry * 0.982  # -1.8%
            
            alert = {
                "event_id": f"{symbol}-{int(time.time())}",
                "signal_id": f"AUTO-{symbol}-{ctx.current_price}",
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "direction": direction,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "risk_pct": 0.6,
                "rr": 3.0,
                "risk_usdt": 10.0,
                "structure_state": 1 if direction == "LONG" else -1,
                
                # Raw context dla analityki
                "raw_context": {
                    "sweep_price": ctx.swing_point.price,
                    "liquidations_count": len(ctx.liquidations),
                    "liquidations_volume_usd": sum(l.volume_usd for l in ctx.liquidations),
                    "obi": ctx.dom_snapshot.obi,
                    "funding_rate": ctx.funding_rate,
                    "confidence_score": score,
                    "delta_strength": ctx.recent_deltas[-1].delta if ctx.recent_deltas else 0
                }
            }
            
            # 7. Wyślij do bot_service
            logger.warning(
                f"🎯 SETUP DETECTED: {symbol} {direction} @ {entry:.2f} | "
                f"Score: {score:.1f}/100 | "
                f"Liqs: ${sum(l.volume_usd for l in ctx.liquidations)/1000:.0f}k | "
                f"OBI: {ctx.dom_snapshot.obi:.2f}"
            )
            
            await send_alert_to_bot(alert)
            
            # Cooldown: prevent duplicate signals
            processor.last_signal_time[symbol] = time.time()
            
            break  # Only one direction at a time
            
        except Exception as e:
            logger.exception(f"[{symbol}] Error evaluating {direction} setup: {e}")
            continue