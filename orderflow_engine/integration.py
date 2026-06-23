# orderflow_engine/integration.py
# WERSJA 7.2 - Ingestion Layer State Management

import asyncio
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
    check_dom_wall, compute_confidence_score, SignalContext,
    SwingPoint, DeltaPoint, DomSnapshot, _liq_event_ts_ms,
)
from orderflow_engine.bot_sender import send_alert_to_bot
from orderflow_engine.risk_levels import calculate_structure_risk_levels
from orderflow_engine.settings import get_trading_session, settings
from shared_lib.signal_mode import (
    footprint_alerts_enabled,
    get_signal_mode,
)

logger = logging.getLogger(__name__)


def _eval_filter_log(msg: str, *args) -> None:
    """Per-filter evaluate logs — only when LOG_EVAL_VERBOSE=true (dev)."""
    if settings.LOG_EVAL_VERBOSE:
        logger.debug(msg, *args)


def _get_min_liq_volume(symbol: str) -> float:
    return 1.0  # TEST MODE - tymczasowe


# ============================================================
# === INGESTION LAYER STATE (Global Cache for Bot Service) ===
# ============================================================
_context_lock = threading.Lock()
_eval_locks: Dict[str, asyncio.Lock] = {}
_eval_locks_mutex = threading.Lock()
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


def _pick_real_wall(dom_full: dict, direction: str, mid_price: Optional[float]):
    walls = dom_full.get("bid_walls", []) if direction == "LONG" else dom_full.get("ask_walls", [])
    if not walls or not mid_price or mid_price <= 0:
        return False, None, None, None
    wall = max(walls, key=lambda w: float(w.get("size", 0) or 0))
    distance_pct = abs(float(wall.get("distance_from_mid", 0) or 0)) / mid_price * 100.0
    return True, float(wall["price"]), float(wall["size"]), round(distance_pct, 6)


def _compute_delta_velocity(processor, sym: str) -> Optional[float]:
    hist = list(processor.delta_history.get(str(sym).upper(), []))
    if len(hist) < 10:
        return None
    deltas = [float(d["delta"]) for d in hist]
    ts = [int(d["timestamp"]) for d in hist]
    dt_s = (ts[-1] - ts[-10]) / 1000.0
    if dt_s <= 0:
        return None
    return (deltas[-1] - deltas[-10]) / dt_s


def _last_liquidation_meta(liquidations: list) -> tuple:
    if not liquidations:
        return None, None
    latest = max(liquidations, key=lambda l: _liq_event_ts_ms(l))
    ts_ms = _liq_event_ts_ms(latest)
    if ts_ms <= 0:
        return str(latest.get("side")), None
    age_s = max(0.0, time.time() - ts_ms / 1000.0)
    return str(latest.get("side")), round(age_s, 3)


def _build_market_features(ctx: SignalContext, processor, risk_levels, div_result: dict) -> dict:
    sym = ctx.symbol
    direction = ctx.direction
    dom_full = processor.get_dom_snapshot(sym)
    ticker = processor.tickers.get(sym, {}) or {}

    bids = dom_full.get("bids") or ctx.dom_snapshot.bids
    asks = dom_full.get("asks") or ctx.dom_snapshot.asks
    bid_vol = sum(float(p) * float(q) for p, q in bids[:10])
    ask_vol = sum(float(p) * float(q) for p, q in asks[:10])

    best_bid = dom_full.get("best_bid")
    best_ask = dom_full.get("best_ask")
    if best_bid is None and bids:
        best_bid = float(bids[0][0])
    if best_ask is None and asks:
        best_ask = float(asks[0][0])
    spread = (float(best_ask) - float(best_bid)) if best_bid is not None and best_ask is not None else None
    mid_price = (float(best_bid) + float(best_ask)) / 2.0 if best_bid is not None and best_ask is not None else None

    wall_detected, wall_price, wall_size, wall_distance_pct = _pick_real_wall(dom_full, direction, mid_price)

    _, buy_v, sell_v = processor._calculate_delta_window_volumes(sym, 300)
    deltas_list = [d.delta for d in ctx.recent_deltas]
    delta_last = float(deltas_list[-1]) if deltas_list else None

    swing = float(ctx.swing_point.price)
    price = float(ctx.current_price)
    if direction == "LONG":
        sweep_depth_pct = ((swing - price) / swing * 100.0) if swing > 0 and price < swing else 0.0
    else:
        sweep_depth_pct = ((price - swing) / swing * 100.0) if swing > 0 and price > swing else 0.0
    distance_to_swing_pct = (abs(price - swing) / swing * 100.0) if swing > 0 else None

    last_liq_side, last_liq_age_s = _last_liquidation_meta(ctx.liquidations)
    oi = ticker.get("open_interest")
    vol_24h = ticker.get("volume_24h")

    div_strength = div_result.get("strength")
    if div_strength is not None:
        div_strength = float(div_strength)

    delta_velocity = _compute_delta_velocity(processor, sym)

    return {
        "obi_value": float(ctx.dom_snapshot.obi),
        "bid_volume_top10": round(bid_vol, 6),
        "ask_volume_top10": round(ask_vol, 6),
        "spread": round(spread, 8) if spread is not None else None,
        "best_bid": float(best_bid) if best_bid is not None else None,
        "best_ask": float(best_ask) if best_ask is not None else None,
        "real_wall_detected": wall_detected,
        "real_wall_price": wall_price,
        "real_wall_size": wall_size,
        "real_wall_distance_pct": wall_distance_pct if wall_detected else None,
        "dom_check_passed": check_dom_wall(ctx),
        "delta_last": delta_last,
        "buy_volume_300s": round(float(buy_v), 6),
        "sell_volume_300s": round(float(sell_v), 6),
        "delta_velocity": round(delta_velocity, 6) if delta_velocity is not None else None,
        "cvd": None,
        "trade_count_300s": processor._count_trades_window(sym, 300),
        "delta_divergence_real": bool(div_result.get("detected", False)),
        "delta_divergence_strength": div_strength if div_result.get("detected") else None,
        "liq_volume_total": round(sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations), 6),
        "matched_liq_volume": round(float(matched_liquidation_volume_usd(ctx)), 6),
        "liq_event_count": len(ctx.liquidations),
        "last_liq_side": last_liq_side,
        "last_liq_age_s": last_liq_age_s,
        "funding_rate": float(ctx.funding_rate) if ctx.funding_rate is not None else None,
        "open_interest": float(oi) if oi not in (None, "", 0) else None,
        "volume_24h": float(vol_24h) if vol_24h not in (None, "", 0) else None,
        "swing_strength": processor.engines[sym].get_swing_strength(direction),
        "sweep_depth_pct": round(sweep_depth_pct, 6),
        "distance_to_swing_pct": round(distance_to_swing_pct, 6) if distance_to_swing_pct is not None else None,
        "tp_was_capped": bool(risk_levels.tp_capped),
        "fallback_sl_used": bool(risk_levels.fallback_used),
        "confidence_score": None,
    }


async def evaluate_and_maybe_alert(symbol: str, processor):
    sym = str(symbol).upper()
    if not footprint_alerts_enabled():
        logger.debug(
            "[evaluate] %s: footprint wyłączony (SIGNAL_MODE=%s)",
            sym,
            get_signal_mode(),
        )
        return
    with _eval_locks_mutex:
        if sym not in _eval_locks:
            _eval_locks[sym] = asyncio.Lock()
    async with _eval_locks[sym]:
        COOLDOWN_SEC = 300
        if time.time() - processor.last_signal_time.get(sym, 0) < COOLDOWN_SEC:
            logger.debug(f"[evaluate] {sym}: cooldown aktywny, pomijam")
            return
        builder = SignalContextBuilder(processor)
        for direction in ["LONG", "SHORT"]:
            eval_failed_at: Optional[str] = None
            try:
                ctx = builder.build_signal_context(sym, direction)
                if ctx is None:
                    continue

                session = get_trading_session()
                if session in settings.SKIP_SESSIONS:
                    logger.debug(
                        f"⏭️ {sym} SKIPPED: session={session} in SKIP_SESSIONS"
                    )
                    eval_failed_at = "session"
                    continue

                div_early = processor._detect_delta_divergence(sym)
                delta_strength = float(div_early.get("strength", 0) or 0)
                if settings.REQUIRE_ZERO_DELTA and delta_strength != 0:
                    logger.debug(
                        f"⏭️ {sym} SKIPPED: delta_strength={delta_strength:.3f} != 0 "
                        f"(REQUIRE_ZERO_DELTA=True)"
                    )
                    eval_failed_at = "delta"
                    continue

                liq_total = sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)
                _eval_filter_log(
                    f"[STRESS-TEST] [{sym}] {direction}: liq_vol=${liq_total:.0f} obi={ctx.dom_snapshot.obi:.3f}"
                )

                if not detect_liquidity_sweep(ctx):
                    _eval_filter_log(f"[FILTER] {sym} {direction}: ❌ liquidity_sweep FAILED")
                    eval_failed_at = "liquidity_sweep"
                    continue
                _eval_filter_log(f"[FILTER] {sym} {direction}: ✅ liquidity_sweep OK")

                liq_threshold = float(processor.LIQUIDATION_CASCADE_THRESHOLD_USD)
                matched = matched_liquidation_volume_usd(ctx)
                if matched < liq_threshold:
                    buffer_total = sum(float(l.get("volume_usd", 0)) for l in ctx.liquidations)
                    _eval_filter_log(
                        f"[FILTER] {sym} {direction}: ❌ liquidations FAILED "
                        f"matched_vol={matched:.0f} buffer_total_usd={buffer_total:.0f} threshold={liq_threshold:.0f}"
                    )
                    eval_failed_at = "liquidations"
                    continue
                _eval_filter_log(
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
                    _eval_filter_log(
                        f"[DELTA_DIAG] {sym} {direction}: samples={n} "
                        f"last_price={last_p:.6f} ref_price={ref_p:.6f} cond_price={cond_p} "
                        f"last_delta={last_d:.4f} ref_delta={ref_d:.4f} cond_delta={cond_d}"
                    )
                else:
                    _eval_filter_log(
                        f"[DELTA_DIAG] {sym} {direction}: samples={n} — za mało punktów (min 10)"
                    )

                _eval_filter_log(f"[{sym}] delta_divergence INPUT: samples={n} direction={direction}")

                if not check_dom_wall(ctx):
                    _eval_filter_log(f"[FILTER] {sym} {direction}: ❌ dom_wall FAILED")
                    eval_failed_at = "dom_wall"
                    continue
                _eval_filter_log(f"[FILTER] {sym} {direction}: ✅ dom_wall OK")

                score = compute_confidence_score(ctx, liq_ok=True, delta_ok=True, dom_ok=True)
                if score < 30:
                    _eval_filter_log(f"[FILTER] {sym} {direction}: ❌ score FAILED score={score:.1f} < 30")
                    eval_failed_at = "score"
                    continue
                _eval_filter_log(f"[FILTER] {sym} {direction}: ✅ score OK score={score:.1f}")

                _now = datetime.now(timezone.utc)
                _session = get_trading_session(_now)
                entry = ctx.current_price
                risk_levels = calculate_structure_risk_levels(
                    sym,
                    direction,
                    entry,
                    processor.engines[sym],
                    score,
                    logger,
                )
                if risk_levels is None:
                    logger.error(f"[FILTER] {sym} {direction}: ❌ structure_risk FAILED")
                    eval_failed_at = "structure_risk"
                    continue

                market_features = _build_market_features(ctx, processor, risk_levels, div_early)
                market_features["confidence_score"] = score

                alert = {
                    "event_id": f"{sym}-{int(time.time())}",
                    "signal_id": f"AUTO-{sym}-{entry}",
                    "symbol": sym,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "direction": direction,
                    "entry": entry,
                    "sl": risk_levels.sl,
                    "tp": risk_levels.tp,
                    "risk_pct": risk_levels.risk_pct,
                    "rr": risk_levels.rr,
                    "risk_usdt": 2.5,
                    "structure_state": 1 if direction == "LONG" else -1,
                    "session": _session,
                    "minute_of_day": _now.hour * 60 + _now.minute,
                    "day_of_week": _now.weekday(),
                    "market_features": market_features,
                    "raw_context": {
                        "swept_swing_level": risk_levels.swing_level,
                        "structure_sl_fallback_used": risk_levels.fallback_used,
                        "structure_tp_capped": risk_levels.tp_capped,
                        "liq_threshold_usd": float(processor.LIQUIDATION_CASCADE_THRESHOLD_USD),
                    },
                }
                await send_alert_to_bot(alert)
                processor.last_signal_time[sym] = time.time()
                break
            except Exception as e:
                logger.error(f"Error evaluating {sym}: {e}")
            finally:
                if settings.LOG_EVAL_VERBOSE and eval_failed_at is not None:
                    logger.debug(
                        "[%s] %s evaluate: failed_at=%s",
                        sym,
                        direction,
                        eval_failed_at,
                    )
