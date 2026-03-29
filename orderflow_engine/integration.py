# orderflow_engine/integration.py
# WERSJA 7.2 - Ingestion Layer State Management

import logging
import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Importy z signal_detector (muszą być tutaj)
from orderflow_engine.signal_detector import (
    detect_liquidity_sweep, check_liquidations, check_delta_divergence,
    check_dom_wall, compute_confidence_score, SignalContext, SwingPoint,
    DeltaPoint, DomSnapshot
)
from orderflow_engine.bot_sender import send_alert_to_bot

logger = logging.getLogger(__name__)

# ============================================================
# === INGESTION LAYER STATE (Global Cache for Bot Service) ===
# ============================================================
_context_lock = threading.Lock()
_symbol_context_cache: Dict[str, Dict[str, Any]] = {}

def update_symbol_context(symbol: str, ctx: Dict[str, Any]) -> None:
    """Aktualizuje globalny stan mikrostruktury dla danego symbolu."""
    with _context_lock:
        _symbol_context_cache[symbol.upper()] = ctx

def get_global_context(symbol: str) -> Optional[Dict[str, Any]]:
    """Pobiera najświeższy stan dla bot_service (używane przez API /context)."""
    with _context_lock:
        return _symbol_context_cache.get(symbol.upper())


class SignalContextBuilder:
    def __init__(self, metrics):
        self.metrics = metrics

    def build_context(self, symbol: str) -> Dict[str, Any]:
        """Zwraca kontekst mikrostruktury dla bot_service. Najpierw z cache."""
        cached = get_global_context(symbol)
        if cached: return cached
        
        # Fallback: Budowanie kontekstu od zera
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ticker = self.metrics.tickers.get(symbol, {})
        dom = self.metrics.get_dom_snapshot(symbol)
        full_ctx = self.metrics.get_full_context(symbol)
        return {
            "symbol": symbol, "timestamp": now, "price": ticker.get('price'),
            "funding_rate": ticker.get('funding_rate', 0.0),
            "structure": full_ctx.get("structure", {}),
            "dom": {"obi": dom.get("obi", 0.0), "bids": dom.get("bids", []), "asks": dom.get("asks", [])},
            "liquidations": self.metrics.get_recent_liquidations(symbol),
            "delta_points": self.metrics.get_recent_deltas(symbol)
        }

    def build_signal_context(self, symbol: str, direction: str) -> SignalContext:
        """Buduje SignalContext dla wewnętrznej logiki signal_detector."""
        engine = self.metrics.engines[symbol]
        current_price = self.metrics.get_last_price(symbol)
        swing_price = (engine.last_swing_low if direction == "LONG" else engine.last_swing_high) or current_price
        
        liqs_raw = self.metrics.get_recent_liquidations(symbol)
        deltas_raw = self.metrics.get_recent_deltas(symbol, limit=30)
        dom_raw = self.metrics.get_dom_snapshot(symbol)
        
        return SignalContext(
            symbol=symbol,
            direction=direction, current_price=current_price,
            swing_point=SwingPoint(price=swing_price, timestamp=datetime.now(timezone.utc)),
            liquidations=list(liqs_raw),
            recent_deltas=[DeltaPoint(price=d['price'], delta=d['delta'], timestamp=d['timestamp']) for d in deltas_raw],
            dom_snapshot=DomSnapshot(bids=dom_raw.get('bids', []), asks=dom_raw.get('asks', []), obi=dom_raw.get('obi', 0.0)),
            funding_rate=self.metrics.get_last_funding(symbol)
        )


def _get_min_liq_volume(symbol: str) -> float:
    """Próg likwidacji dostosowany do klasy aktywu."""
    BTC_ETH = {"BTCUSDT", "ETHUSDT", "BTCPERP", "ETHPERP"}
    MID_CAPS = {"SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"}
    if symbol.upper() in BTC_ETH:
        return 75_000.0
    elif symbol.upper() in MID_CAPS:
        return 25_000.0
    else:
        return 10_000.0


async def evaluate_and_maybe_alert(symbol: str, processor):
    COOLDOWN_SEC = 300
    if time.time() - processor.last_signal_time.get(symbol, 0) < COOLDOWN_SEC:
        logger.debug(f"[evaluate] {symbol}: cooldown aktywny, pomijam")
        return
    builder = SignalContextBuilder(processor)
    for direction in ["LONG", "SHORT"]:
        try:
            ctx = builder.build_signal_context(symbol, direction)
            
            if not detect_liquidity_sweep(ctx):
                logger.info(f"[FILTER] {symbol} {direction}: ❌ liquidity_sweep FAILED")
                continue
            logger.info(f"[FILTER] {symbol} {direction}: ✅ liquidity_sweep OK")
            
            liq_threshold = _get_min_liq_volume(symbol)
            if not check_liquidations(ctx, min_volume_usd=liq_threshold):
                liq_vol = sum(float(l.get('volume_usd', 0)) for l in ctx.liquidations)
                logger.info(f"[FILTER] {symbol} {direction}: ❌ liquidations FAILED vol={liq_vol:.0f} threshold={liq_threshold:.0f}")
                continue
            logger.info(f"[FILTER] {symbol} {direction}: ✅ liquidations OK")
            
            if not check_delta_divergence(ctx):
                logger.info(f"[FILTER] {symbol} {direction}: ❌ delta_divergence FAILED")
                continue
            logger.info(f"[FILTER] {symbol} {direction}: ✅ delta_divergence OK")
            
            if not check_dom_wall(ctx):
                logger.info(f"[FILTER] {symbol} {direction}: ❌ dom_wall FAILED")
                continue
            logger.info(f"[FILTER] {symbol} {direction}: ✅ dom_wall OK")
            
            score = compute_confidence_score(ctx, liq_ok=True, delta_ok=True, dom_ok=True)
            if score < 70:
                logger.info(f"[FILTER] {symbol} {direction}: ❌ score FAILED score={score:.1f} < 70")
                continue
            logger.info(f"[FILTER] {symbol} {direction}: ✅ score OK score={score:.1f}")

            entry = ctx.current_price
            alert = {
                "event_id": f"{symbol}-{int(time.time())}", "signal_id": f"AUTO-{symbol}-{entry}",
                "symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "direction": direction, "entry": entry,
                "sl": entry * 0.994 if direction == "LONG" else entry * 1.006,
                "tp": entry * 1.018 if direction == "LONG" else entry * 0.982,
                "risk_pct": 0.6, "rr": 3.0, "risk_usdt": 10.0, "structure_state": 1 if direction == "LONG" else -1,
                "raw_context": {"confidence_score": score, "obi": ctx.dom_snapshot.obi, "liq_vol": sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)}
            }
            await send_alert_to_bot(alert)
            processor.last_signal_time[symbol] = time.time()
            break 
        except Exception as e:
            logger.error(f"Error evaluating {symbol}: {e}")