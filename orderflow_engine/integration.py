# orderflow_engine/integration.py
# WERSJA 7.2 - Ingestion Layer State Management

import logging
import math
import os
import queue
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


def _get_min_liq_volume(symbol: str) -> float:
    return 1.0  # TEST MODE - tymczasowe


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
# Domyślnie wyłączone — zapis na każdą aktualizację kontekstu podnosi koszt i ryzyko regresji; włącz jawnie na multi‑replica.
ORDERFLOW_CONTEXT_USE_FIRESTORE = os.environ.get(
    "ORDERFLOW_CONTEXT_USE_FIRESTORE", "false"
).strip().lower() in ("1", "true", "yes", "on")
CONTEXT_FS_QUEUE_MAX = int(os.environ.get("CONTEXT_FS_QUEUE_MAX", "2000"))

_fs_write_queue: Optional[queue.Queue] = None
_fs_writer_thread: Optional[threading.Thread] = None
_fs_writer_lock = threading.Lock()


def set_context_firestore_client(client) -> None:
    """Wywołaj z main po initialize_firestore — współdzielony kontekst między replikami Cloud Run."""
    global _firestore_client
    _firestore_client = client
    if client is not None and ORDERFLOW_CONTEXT_USE_FIRESTORE:
        _ensure_firestore_writer_thread()


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


def _firestore_writer_main() -> None:
    from google.cloud import firestore as gcf

    while True:
        item = _fs_write_queue.get()
        sym_log = item[0] if (item is not None and isinstance(item, tuple) and len(item) >= 1) else "?"
        try:
            if item is None:
                return
            sym, ctx = item
            client = _firestore_client
            if client is None:
                continue
            payload = _sanitize_for_firestore(ctx)
            ref = client.collection(ORDERFLOW_CONTEXT_FS_COLLECTION).document(sym)
            ref.set(
                {"data": payload, "updated_at": gcf.SERVER_TIMESTAMP, "symbol": sym},
                merge=False,
            )
        except Exception as e:
            logger.warning("Firestore context persist failed symbol=%s: %s", sym_log, e)
        finally:
            _fs_write_queue.task_done()


def _ensure_firestore_writer_thread() -> None:
    global _fs_write_queue, _fs_writer_thread
    with _fs_writer_lock:
        if _fs_writer_thread is not None and _fs_writer_thread.is_alive():
            return
        _fs_write_queue = queue.Queue(maxsize=CONTEXT_FS_QUEUE_MAX)
        _fs_writer_thread = threading.Thread(
            target=_firestore_writer_main,
            name="orderflow-fs-context-writer",
            daemon=True,
        )
        _fs_writer_thread.start()
        logger.info("Uruchomiono wątek zapisu kontekstu do Firestore (poza pętlą asyncio).")


def _enqueue_symbol_context_firestore(sym: str, ctx: Dict[str, Any]) -> None:
    if not ORDERFLOW_CONTEXT_USE_FIRESTORE or _firestore_client is None:
        return
    _ensure_firestore_writer_thread()
    assert _fs_write_queue is not None
    try:
        _fs_write_queue.put_nowait((sym, ctx))
    except queue.Full:
        logger.warning(
            "Kolejka zapisu Firestore pełna (max=%s), pomijam persist symbol=%s",
            CONTEXT_FS_QUEUE_MAX,
            sym,
        )


def _persist_symbol_context_firestore(sym: str, ctx: Dict[str, Any]) -> None:
    if not ORDERFLOW_CONTEXT_USE_FIRESTORE or _firestore_client is None:
        return
    now = time.time()
    with _fs_persist_lock:
        if now - _last_firestore_persist_ts.get(sym, 0) < CONTEXT_FIRESTORE_MIN_INTERVAL_SEC:
            return
        _last_firestore_persist_ts[sym] = now
    _enqueue_symbol_context_firestore(sym, ctx)


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


def prime_context_memory(symbol: str, ctx: Dict[str, Any]) -> None:
    """Wypełnia cache RAM bez ponownego zapisu do Firestore (np. po odczycie z innej repliki)."""
    with _context_lock:
        _symbol_context_cache[symbol.upper()] = ctx


def load_remote_context_firestore(symbol: str) -> Optional[Dict[str, Any]]:
    """Blokujący odczyt z Firestore — używaj wyłącznie przez asyncio.to_thread w handlerze HTTP."""
    return _load_symbol_context_firestore(symbol.upper())


def get_global_context(symbol: str) -> Optional[Dict[str, Any]]:
    """Wyłącznie cache w RAM — nigdy synchronicznego Firestore (ścieżka WebSocket / hot path)."""
    sym = symbol.upper()
    with _context_lock:
        return _symbol_context_cache.get(sym)


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

            deltas = ctx.recent_deltas
            n = len(deltas)
            if n >= 10:
                prices = [d.price for d in deltas]
                dvals = [d.delta for d in deltas]
                last_p = prices[-1]
                last_d = dvals[-1]
                if ctx.direction == "LONG":
                    ref_p = min(prices[-10:-1])
                    ref_d = min(dvals[-10:-1])
                    cond_p = last_p < ref_p
                    cond_d = last_d > ref_d
                else:
                    ref_p = max(prices[-10:-1])
                    ref_d = max(dvals[-10:-1])
                    cond_p = last_p > ref_p
                    cond_d = last_d < ref_d
                logger.info(
                    f"[DELTA_DIAG] {sym} {direction}: samples={n} "
                    f"last_price={last_p:.6f} ref_price={ref_p:.6f} cond_price={cond_p} "
                    f"last_delta={last_d:.4f} ref_delta={ref_d:.4f} cond_delta={cond_d}"
                )
            else:
                logger.info(
                    f"[DELTA_DIAG] {sym} {direction}: samples={n} — za mało punktów (min 10)"
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
                "risk_pct": 0.6, "rr": 3.0, "risk_usdt": 2.5, "structure_state": 1 if direction == "LONG" else -1,
                "raw_context": {"confidence_score": score, "obi": ctx.dom_snapshot.obi, "liq_vol": sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)}
            }
            await send_alert_to_bot(alert)
            processor.last_signal_time[sym] = time.time()
            break 
        except Exception as e:
            logger.error(f"Error evaluating {sym}: {e}")
