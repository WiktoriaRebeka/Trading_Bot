# orderflow_engine/integration.py
# WERSJA 7.2 - Ingestion Layer State Management

import logging
import math
import os
import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Importy z signal_detector (muszą być tutaj)
from orderflow_engine.signal_detector import (
    detect_liquidity_sweep, matched_liquidation_volume_usd,
    check_delta_divergence, check_dom_wall, compute_confidence_score, SignalContext,
    SwingPoint, DeltaPoint, DomSnapshot,
)
from orderflow_engine.bot_sender import send_alert_to_bot

logger = logging.getLogger(__name__)

# ============================================================
# === INGESTION LAYER STATE (Global Cache for Bot Service) ===
# ============================================================
_context_lock = threading.Lock()
_symbol_context_cache: Dict[str, Dict[str, Any]] = {}
_fs_persist_lock = threading.Lock()
_last_firestore_persist_ts: Dict[str, float] = {}
_firestore_client = None

ORDERFLOW_CONTEXT_FS_COLLECTION = os.environ.get(
    "ORDERFLOW_CONTEXT_FS_COLLECTION", "orderflow_symbol_context"
)
CONTEXT_FIRESTORE_MIN_INTERVAL_SEC = float(
    os.environ.get("CONTEXT_FIRESTORE_MIN_INTERVAL_SEC", "0.25")
)
ORDERFLOW_CONTEXT_USE_FIRESTORE = os.environ.get(
    "ORDERFLOW_CONTEXT_USE_FIRESTORE", "true"
).strip().lower() in ("1", "true", "yes", "on")


def set_context_firestore_client(client) -> None:
    """Wywołaj z main po initialize_firestore — współdzielony kontekst między replikami Cloud Run."""
    global _firestore_client
    _firestore_client = client


def _sanitize_for_firestore(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_firestore(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_firestore(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _persist_symbol_context_firestore(sym: str, ctx: Dict[str, Any]) -> None:
    if not ORDERFLOW_CONTEXT_USE_FIRESTORE or _firestore_client is None:
        return
    now = time.time()
    with _fs_persist_lock:
        if now - _last_firestore_persist_ts.get(sym, 0) < CONTEXT_FIRESTORE_MIN_INTERVAL_SEC:
            return
        _last_firestore_persist_ts[sym] = now
    try:
        from google.cloud import firestore as gcf

        payload = _sanitize_for_firestore(ctx)
        ref = _firestore_client.collection(ORDERFLOW_CONTEXT_FS_COLLECTION).document(sym)
        ref.set(
            {"data": payload, "updated_at": gcf.SERVER_TIMESTAMP, "symbol": sym},
            merge=False,
        )
    except Exception as e:
        logger.warning("Firestore context persist failed symbol=%s: %s", sym, e)


def _load_symbol_context_firestore(sym: str) -> Optional[Dict[str, Any]]:
    if not ORDERFLOW_CONTEXT_USE_FIRESTORE or _firestore_client is None:
        return None
    try:
        snap = _firestore_client.collection(ORDERFLOW_CONTEXT_FS_COLLECTION).document(sym).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        ctx = data.get("data")
        return ctx if isinstance(ctx, dict) else None
    except Exception as e:
        logger.warning("Firestore context read failed symbol=%s: %s", sym, e)
        return None


def update_symbol_context(symbol: str, ctx: Dict[str, Any]) -> None:
    """Aktualizuje globalny stan mikrostruktury dla danego symbolu."""
    sym = symbol.upper()
    with _context_lock:
        _symbol_context_cache[sym] = ctx
    _persist_symbol_context_firestore(sym, ctx)


def get_global_context(symbol: str) -> Optional[Dict[str, Any]]:
    """Pobiera najświeższy stan dla bot_service (używane przez API /context)."""
    sym = symbol.upper()
    with _context_lock:
        hit = _symbol_context_cache.get(sym)
        if hit:
            return hit
    remote = _load_symbol_context_firestore(sym)
    if remote:
        with _context_lock:
            _symbol_context_cache[sym] = remote
        return remote
    return None


class SignalContextBuilder:
    def __init__(self, metrics):
        self.metrics = metrics

    def build_context(self, symbol: str) -> Dict[str, Any]:
        """Zwraca kontekst mikrostruktury dla bot_service. Najpierw z cache."""
        sym = str(symbol).upper()
        cached = get_global_context(sym)
        if cached: return cached
        
        # Fallback: Budowanie kontekstu od zera
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ticker = self.metrics.tickers.get(sym, {})
        dom = self.metrics.get_dom_snapshot(sym)
        full_ctx = self.metrics.get_full_context(sym)
        return {
            "symbol": sym, "timestamp": now, "price": ticker.get('price'),
            "funding_rate": ticker.get('funding_rate', 0.0),
            "structure": full_ctx.get("structure", {}),
            "dom": {"obi": dom.get("obi", 0.0), "bids": dom.get("bids", []), "asks": dom.get("asks", [])},
            "liquidations": self.metrics.get_recent_liquidations(sym),
            "delta_points": self.metrics.get_recent_deltas(sym)
        }

    def build_signal_context(self, symbol: str, direction: str) -> SignalContext:
        """Buduje SignalContext dla wewnętrznej logiki signal_detector."""
        sym = str(symbol).upper()
        engine = self.metrics.engines[sym]
        current_price = self.metrics.get_last_price(sym)
        raw_swing = engine.last_swing_low if direction == "LONG" else engine.last_swing_high
        if raw_swing is None:
            logger.debug(f"[{sym}] {direction}: brak swing point — pomijam ewaluację")
            return None
        swing_price = raw_swing
        
        liqs_raw = self.metrics.get_recent_liquidations(sym)
        deltas_raw = self.metrics.get_recent_deltas(sym, limit=30)
        dom_raw = self.metrics.get_dom_snapshot(sym)
        
        return SignalContext(
            symbol=sym,
            direction=direction, current_price=current_price,
            swing_point=SwingPoint(price=swing_price, timestamp=datetime.now(timezone.utc)),
            liquidations=list(liqs_raw),
            recent_deltas=[DeltaPoint(price=d['price'], delta=d['delta'], timestamp=d['timestamp']) for d in deltas_raw],
            dom_snapshot=DomSnapshot(bids=dom_raw.get('bids', []), asks=dom_raw.get('asks', []), obi=dom_raw.get('obi', 0.0)),
            funding_rate=self.metrics.get_last_funding(sym)
        )


async def evaluate_and_maybe_alert(symbol: str, processor):
    sym = str(symbol).upper()
    COOLDOWN_SEC = 300
    if time.time() - processor.last_signal_time.get(sym, 0) < COOLDOWN_SEC:
        logger.debug(f"[evaluate] {sym}: cooldown aktywny, pomijam")
        return
    builder = SignalContextBuilder(processor)
    for direction in ["LONG", "SHORT"]:
        try:
            ctx = builder.build_signal_context(sym, direction)
            if ctx is None:
                continue
            
            if not detect_liquidity_sweep(ctx):
                logger.info(f"[FILTER] {sym} {direction}: ❌ liquidity_sweep FAILED")
                continue
            logger.info(f"[FILTER] {sym} {direction}: ✅ liquidity_sweep OK")

            liq_threshold = float(processor.LIQUIDATION_CASCADE_THRESHOLD_USD)
            matched = matched_liquidation_volume_usd(ctx)
            if matched < liq_threshold:
                buffer_total = sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)
                logger.info(
                    f"[FILTER] {sym} {direction}: ❌ liquidations FAILED "
                    f"matched_vol={matched:.0f} buffer_total_usd={buffer_total:.0f} threshold={liq_threshold:.0f}"
                )
                continue
            logger.info(
                f"[FILTER] {sym} {direction}: ✅ liquidations OK matched_vol={matched:.0f} threshold={liq_threshold:.0f}"
            )

            if not check_delta_divergence(ctx):
                logger.info(f"[FILTER] {sym} {direction}: ❌ delta_divergence FAILED")
                continue
            logger.info(f"[FILTER] {sym} {direction}: ✅ delta_divergence OK")
            
            if not check_dom_wall(ctx):
                logger.info(f"[FILTER] {sym} {direction}: ❌ dom_wall FAILED")
                continue
            logger.info(f"[FILTER] {sym} {direction}: ✅ dom_wall OK")
            
            score = compute_confidence_score(ctx, liq_ok=True, delta_ok=True, dom_ok=True)
            if score < 70:
                logger.info(f"[FILTER] {sym} {direction}: ❌ score FAILED score={score:.1f} < 70")
                continue
            logger.info(f"[FILTER] {sym} {direction}: ✅ score OK score={score:.1f}")

            entry = ctx.current_price
            alert = {
                "event_id": f"{sym}-{int(time.time())}", "signal_id": f"AUTO-{sym}-{entry}",
                "symbol": sym, "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "direction": direction, "entry": entry,
                "sl": entry * 0.994 if direction == "LONG" else entry * 1.006,
                "tp": entry * 1.018 if direction == "LONG" else entry * 0.982,
                "risk_pct": 0.6, "rr": 3.0, "risk_usdt": 10.0, "structure_state": 1 if direction == "LONG" else -1,
                "raw_context": {"confidence_score": score, "obi": ctx.dom_snapshot.obi, "liq_vol": sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)}
            }
            await send_alert_to_bot(alert)
            processor.last_signal_time[sym] = time.time()
            break 
        except Exception as e:
            logger.error(f"Error evaluating {sym}: {e}")