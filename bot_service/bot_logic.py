# Lokalizacja: bot_service/bot_logic.py
import logging
import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext
from concurrent.futures import ThreadPoolExecutor

from flask import current_app

# absolutne importy z shared_lib / bot_service
from shared_lib.models import AlertData
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size, round_qty_by_step
from bot_service import state_manager
from bot_service.bybit_executor import BybitExecutor
from bot_service.fetch_from_firestore import load_last_processed_timestamp, save_last_processed_timestamp
from bot_service.pnl_logger_real import log_real_trade_result
from bot_service.bigquery_logger import log_analysis_result

# precyzja Decimal globalnie
getcontext().prec = 28

logger = logging.getLogger(__name__)

# ThreadPool do asynchronicznego logowania do BigQuery (nie blokuje głównego flow)
_bq_executor = ThreadPoolExecutor(max_workers=2)


# =====================================================================
# === 1. HELPERS (Production Hardening & Safety) ===
# =====================================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    """Zabezpiecza przed crashami przy danych z API (None, stringi)."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def call_with_retry(fn, *args, retries: int = 3, backoff: float = 0.3, **kwargs):
    """Resilient wrapper dla wywołań zewnętrznych (Bybit/OrderFlow)."""
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            attempt += 1
            logger.warning(json.dumps({"stage": "executor_call", "fn": getattr(fn, "__name__", str(fn)), "attempt": attempt, "error": str(e)}))
            if attempt >= retries:
                logger.error(json.dumps({"stage": "executor_failed", "fn": getattr(fn, "__name__", str(fn)), "error": str(e)}))
                raise
            time.sleep(backoff * (2 ** (attempt - 1)))


def log_struct(level: str, stage: str, msg: str, **fields):
    """Ustrukturyzowany logger JSON dla Cloud Logging / Stackdriver."""
    entry = {"stage": stage, "msg": msg}
    entry.update(fields)
    if level == "info":
        logger.info(json.dumps(entry))
    elif level == "warning":
        logger.warning(json.dumps(entry))
    else:
        logger.error(json.dumps(entry))


def _log_analysis_result_bg(data: Dict[str, Any]):
    """Background wrapper for BigQuery logging to avoid blocking request flow."""
    try:
        log_analysis_result(data)
    except Exception:
        logger.exception(json.dumps({"stage": "bq_bg_log_failed", "event_id": data.get("event_id")}))


# =====================================================================
# === 2. ROUNDING LOGIC (Deterministyczna) ===
# =====================================================================

def round_price_by_tick(price: float, tick_size: str, direction: str = "none") -> float:
    """
    Deterministic Decimal-only rounding to tick_size.
    - direction: 'down' | 'up' | 'none' (nearest)
    """
    p_dec = Decimal(str(price))
    t_dec = Decimal(str(tick_size))
    if t_dec == 0:
        raise ValueError("tick_size cannot be zero")

    ticks = p_dec / t_dec
    int_part = ticks.to_integral_value(rounding=ROUND_DOWN)
    frac = ticks - int_part

    if direction == "down":
        q_ticks = int_part
    elif direction == "up":
        q_ticks = ticks.to_integral_value(rounding=ROUND_UP)
    else:
        # deterministic: fractional >= 0.5 -> up, else down
        q_ticks = int_part + (Decimal(1) if frac >= Decimal("0.5") else Decimal(0))

    quantized_price = q_ticks * t_dec
    return float(quantized_price)


# =====================================================================
# === 3. MAIN SIGNAL HANDLER (Autonomous) ===
# =====================================================================
def handle_immediate_signal(payload: Dict[str, Any], executor: BybitExecutor) -> None:
    start_ts = datetime.now(timezone.utc)
    event_id = "unknown"
    symbol = None

    # ============================================================
    # 1. Parse alert
    # ============================================================
    try:
        alert = AlertData.model_validate(payload)
        event_id = alert.event_id
        symbol = alert.symbol
    except Exception as e:
        logger.exception(json.dumps({
            "event_id": event_id,
            "symbol": symbol,
            "stage": "alert_validation",
            "error": str(e)
        }))
        return

    # ============================================================
    # 2. Fetch microstructure context from OrderFlow Engine
    # ============================================================
    orderflow_client = current_app.config.get("ORDERFLOW_CLIENT")
    try:
        micro_ctx = orderflow_client.get_context(symbol)
        log_struct(
            "info", "orderflow_context", "Pobrano kontekst mikrostruktury",
            symbol=symbol, event_id=event_id, context_available=True
        )
    except Exception as e:
        micro_ctx = None
        log_struct(
            "warning", "orderflow_context", "Nie udało się pobrać kontekstu",
            symbol=symbol, event_id=event_id, error=str(e)
        )

    # ============================================================
    # 3. Microstructure filters (safety layer)
    # ============================================================
    if micro_ctx:
        obi = micro_ctx["dom"]["obi"]
        liq_count = len(micro_ctx["liquidations"])
        delta_points = micro_ctx["delta_points"]
        last_delta = delta_points[-1]["delta"] if delta_points else 0

        # 1) OBI filter
        if abs(obi) < 0.1:
            log_struct("warning", "context_filter", "OBI zbyt słabe — odrzucam sygnał",
                       symbol=symbol, event_id=event_id, obi=obi)
            return

        # 2) Liquidations filter
        if liq_count < 2:
            log_struct("warning", "context_filter", "Za mało likwidacji — odrzucam sygnał",
                       symbol=symbol, event_id=event_id, liq_count=liq_count)
            return

        # 3) Delta filter
        if abs(last_delta) < 5000:
            log_struct("warning", "context_filter", "Delta zbyt słaba — odrzucam sygnał",
                       symbol=symbol, event_id=event_id, last_delta=last_delta)
            return

    # ============================================================
    # 4. Microstructure scoring (redundant safety layer)
    # ============================================================
    micro_score = 0

    if micro_ctx:
        # OBI
        if abs(micro_ctx["dom"]["obi"]) > 0.25:
            micro_score += 30

        # Liquidations
        if len(micro_ctx["liquidations"]) >= 3:
            micro_score += 30

        # Delta
        if micro_ctx["delta_points"]:
            if abs(micro_ctx["delta_points"][-1]["delta"]) > 8000:
                micro_score += 20

        # Structure
        if micro_ctx["structure"]["last_swing_low"] or micro_ctx["structure"]["last_swing_high"]:
            micro_score += 20

    log_struct("info", "micro_score", "Microstructure score computed",
               symbol=symbol, event_id=event_id, micro_score=micro_score)

    if micro_score < 60:
        log_struct("warning", "micro_score", "Microstructure score too low — rejecting signal",
                   symbol=symbol, event_id=event_id, micro_score=micro_score)
        return

    # ============================================================
    # 5. Double-trade guard
    # ============================================================
    try:
        if call_with_retry(executor.get_open_position_side, symbol):
            log_struct("warning", "signal_input", "Position already open",
                       symbol=symbol, event_id=event_id)
            return
    except Exception as e:
        log_struct("error", "signal_input", "Executor check failed",
                   symbol=symbol, event_id=event_id, error=str(e))
        return

    # ============================================================
    # 6. Rules & tick/qty validation
    # ============================================================
    rules = get_instrument_rules().get(symbol)
    if not rules:
        log_struct("error", "signal_input", "Rules missing in Firestore",
                   symbol=symbol, event_id=event_id)
        return

    tick_size = str(rules.get("tickSize"))
    qty_step = str(rules.get("qtyStep"))

    try:
        if Decimal(str(tick_size)) == 0 or Decimal(str(qty_step)) == 0:
            log_struct("error", "signal_input", "Invalid tick/qty step in rules",
                       symbol=symbol, event_id=event_id,
                       tick_size=tick_size, qty_step=qty_step)
            return
    except Exception as e:
        log_struct("error", "signal_input", "Invalid rules format",
                   symbol=symbol, event_id=event_id, error=str(e))
        return

    # ============================================================
    # 7. Rounding & Sizing
    # ============================================================
    is_long = alert.direction.upper() == "LONG"
    f_entry = round_price_by_tick(alert.entry, tick_size, "down" if is_long else "up")
    f_sl = round_price_by_tick(alert.sl, tick_size, "up" if is_long else "down")
    f_tp = round_price_by_tick(alert.tp, tick_size, "down" if is_long else "up")

    raw_qty = calculate_position_size(
        risk_per_trade_usdt=alert.risk_usdt,
        entry_price=f_entry,
        sl_price=f_sl,
        qty_step=qty_step
    )
    final_qty = round_qty_by_step(raw_qty, qty_step)

    log_struct("info", "signal_input", "Qty computed",
               symbol=symbol, event_id=event_id,
               raw_qty=float(raw_qty), final_qty=final_qty)

    if final_qty <= 0:
        log_struct("error", "signal_input", "Final Qty is zero",
                   symbol=symbol, event_id=event_id)
        return

    # ============================================================
    # 8. Execution params (DRY RUN)
    # ============================================================
    order_params = {
        "symbol": symbol,
        "side": "Buy" if is_long else "Sell",
        "orderType": "Limit",
        "qty": str(final_qty),
        "price": str(f_entry),
        "stopLoss": str(f_sl),
        "takeProfit": str(f_tp),
        "orderLinkId": event_id
    }

    log_struct("info", "execution", "Dry run prepared",
               symbol=symbol, event_id=event_id, params=order_params)

    # ============================================================
    # 9. Persistence (Firestore)
    # ============================================================
    state_payload = {
        "symbol": symbol,
        "status": "DRY RUN LOG",
        "direction": alert.direction.upper(),
        "planned_qty": final_qty,
        "params": order_params,
        "event_id": event_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "planned_sl_price": f_sl,
        "planned_tp_price": f_tp
    }

    try:
        if hasattr(state_manager, "save_active_order_transactional"):
            state_manager.save_active_order_transactional(event_id, state_payload)
        else:
            state_manager.save_active_order(event_id, state_payload)
    except Exception as e:
        log_struct("error", "persistence", "Failed to save active order",
                   symbol=symbol, event_id=event_id, error=str(e))
        return

    # ============================================================
    # 10. Analytics (BigQuery)
    # ============================================================
    analysis_data = {
        "event_id": event_id,
        "signal_id": alert.signal_id,
        "symbol": symbol,
        "timestamp": alert.timestamp,
        "direction": alert.direction.upper(),
        "entry": f_entry,
        "sl": f_sl,
        "tp": f_tp,
        "risk_pct": alert.risk_pct,
        "rr": alert.rr,
        "structure_state": alert.structure_state,
        "risk_usdt": alert.risk_usdt,
        "raw_context": alert.raw_context if isinstance(alert.raw_context, dict) else {},
        "microstructure": micro_ctx if micro_ctx else {}
    }

    _bq_executor.submit(_log_analysis_result_bg, analysis_data)

    # ============================================================
    # 11. Final log
    # ============================================================
    duration_ms = int((datetime.now(timezone.utc) - start_ts).total_seconds() * 1000)
    log_struct("info", "signal_input", "Processing completed",
               symbol=symbol, event_id=event_id, duration_ms=duration_ms)

# =====================================================================
# === 4. ORDER UPDATER (Management) ===
# =====================================================================

def update_filled_orders(executor: BybitExecutor):
    """Cykl zarządzania otwartymi zleceniami i Trailing Stopem."""
    log_struct("info", "updater", "Cycle started")

    # --- CZĘŚĆ 1: Obsługa zleceń oczekujących na wejście (status: PLACED) ---
    placed_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_docs = list(state_manager.get_orders_without_status())
    orders_to_check = placed_docs + legacy_docs

    log_struct("info", "updater", "Placed orders count", count=len(orders_to_check))

    for doc in orders_to_check:
        data = doc.to_dict()
        order_link_id = doc.id
        symbol = data.get('symbol')

        if not symbol:
            log_struct("error", "updater", "Missing symbol in order doc", event_id=order_link_id)
            state_manager.update_active_order(order_link_id, {'status': 'ERROR_DATA_MISSING'})
            continue

        try:
            details = call_with_retry(executor.find_order_details_by_link_id, symbol, order_link_id)
            if not details:
                # increment retry counter
                retry_count = data.get('placed_check_retries', 0)
                if retry_count < 5:
                    state_manager.update_active_order(order_link_id, {'placed_check_retries': retry_count + 1})
                    log_struct("warning", "updater", "Order details not found, will retry", symbol=symbol, event_id=order_link_id, attempt=retry_count + 1)
                else:
                    state_manager.update_active_order(order_link_id, {'status': 'UNKNOWN'})
                    log_struct("error", "updater", "Order details not found after retries", symbol=symbol, event_id=order_link_id)
                continue

            status = details.get('orderStatus')
            log_struct("info", "updater", "Order status fetched", symbol=symbol, event_id=order_link_id, order_status=status)

            if status == 'Filled':
                pos = call_with_retry(executor.get_position_info, symbol)
                if pos and safe_float(pos.get('size')) > 0:
                    sl_id = executor.find_sl_order_id(symbol, data)
                    state_manager.update_active_order(order_link_id, {
                        'status': 'OPEN',
                        'slOrderId': sl_id,
                        'position_opened_at': datetime.now(timezone.utc).isoformat()
                    })
                    log_struct("info", "updater", "Order moved to OPEN", symbol=symbol, event_id=order_link_id, slOrderId=sl_id)
                else:
                    state_manager.update_active_order(order_link_id, {'status': 'CLOSED_UNVERIFIED'})
                    log_struct("error", "updater", "Filled but no position found", symbol=symbol, event_id=order_link_id)

            elif status in ['Cancelled', 'Rejected']:
                state_manager.delete_active_order_by_id(order_link_id)
                log_struct("info", "updater", "Order removed (cancelled/rejected)", symbol=symbol, event_id=order_link_id)

            else:
                # ensure status field exists
                if 'status' not in data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

        except Exception as e:
            log_struct("error", "updater", "Failed to update placed order", event_id=order_link_id, error=str(e))


# =====================================================================
# === 5. PNL LOGGER (Reporting) ===
# =====================================================================

def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    """Cykl zamykania pozycji i raportowania do BigQuery."""
    last_ts = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    if last_ts is None:
        # default to now - 24h if missing
        last_ts = datetime.now(timezone.utc) - timedelta(hours=24)
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    start_ms = int((last_ts - timedelta(hours=24)).timestamp() * 1000)
    processed = 0

    try:
        records = call_with_retry(executor.get_closed_pnl_history, start_time_ms=start_ms)
        if not records:
            log_struct("info", "pnl_logger", "No closed pnl records found")
            save_last_processed_timestamp(datetime.now(timezone.utc), "pnl_logger_last_fetch_state")
            return 0

        for rec in records:
            order_id = rec.get("orderId")
            if state_manager.is_pnl_record_processed(order_id):
                continue

            matched_order = _find_matching_order(rec)
            try:
                if log_real_trade_result(rec, matched_order):
                    processed += 1
            except Exception as e:
                log_struct("error", "pnl_logger", "Failed to log real trade result", order_id=order_id, error=str(e))

        save_last_processed_timestamp(datetime.now(timezone.utc), "pnl_logger_last_fetch_state")
        log_struct("info", "pnl_logger", "Cycle completed", processed=processed)
        return processed

    except Exception as e:
        log_struct("error", "pnl_logger", "Cycle failed", error=str(e))
        return 0


def _find_matching_order(pnl_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dopasowuje rekord zamknięcia do zlecenia w Firestore."""
    # 1. Match by orderLinkId (event_id)
    link_id = pnl_record.get("orderLinkId")
    if link_id:
        match = state_manager.get_active_order_by_id(link_id)
        if match:
            return match

    # 2. Match by sl order id
    closing_order_id = pnl_record.get("orderId")
    if closing_order_id:
        match = state_manager.get_active_order_by_sl_order_id(closing_order_id)
        if match:
            return match

    # 3. Fallback: latest active order for symbol & side
    symbol = pnl_record.get("symbol")
    side = "LONG" if pnl_record.get("side") == "Buy" else "SHORT"
    return state_manager.get_latest_active_order_for_symbol(symbol, side)