# Lokalizacja: bot_service/bot_logic.py
import asyncio
import logging
import json
import time
from typing import Dict, Any, Optional, List, Callable, Awaitable, TypeVar
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
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


T = TypeVar("T")


async def async_call_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    event_id: str,
    retries: int = 3,
    backoff: float = 0.3,
) -> T:
    """Retry z asyncio.sleep (bez time.sleep) — każdy log zawiera event_id."""
    for attempt in range(1, retries + 1):
        try:
            return await factory()
        except Exception as e:
            logger.warning(f"[{event_id}] async_call_with_retry attempt {attempt}/{retries}: {e}")
            if attempt >= retries:
                logger.error(f"[{event_id}] async_call_with_retry exhausted after {retries} attempts: {e}")
                raise
            await asyncio.sleep(backoff * (2 ** (attempt - 1)))
    raise RuntimeError(f"[{event_id}] async_call_with_retry: unreachable")


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

async def handle_immediate_signal(payload: Dict[str, Any], executor: BybitExecutor) -> None:
    start_total = perf_counter()
    event_id = payload.get("event_id", "unknown")
    symbol = payload.get("symbol", "unknown")

    # 1. Walidacja sygnału
    try:
        signal = AlertData.model_validate(payload)
    except Exception as e:
        logger.error(f"[{event_id}] alert_validation: Błąd walidacji payloadu symbol={symbol} error={e}")
        return

    # 2. Pobieranie kontekstu OrderFlow (opcjonalnie)
    orderflow_client = current_app.config.get("ORDERFLOW_CLIENT")
    micro_ctx = None
    if orderflow_client:
        try:
            micro_ctx = await asyncio.to_thread(orderflow_client.get_context, symbol)
        except Exception as e:
            logger.error(f"[{event_id}] orderflow_context: Błąd pobierania kontekstu symbol={symbol} error={e}")

    # 3. Double-trade guard
    try:

        async def _fetch_open_side() -> Optional[str]:
            return await asyncio.to_thread(executor.get_open_position_side, symbol)

        open_side = await async_call_with_retry(_fetch_open_side, event_id=event_id)
        if open_side:
            logger.warning(f"[{event_id}] signal_input: REJECT — pozycja już otwarta symbol={symbol} open_side={open_side}")
            return
    except Exception as e:
        logger.error(f"[{event_id}] signal_input: Błąd sprawdzania pozycji symbol={symbol} error={e}")
        return

    # 4. Reguły instrumentu (Firestore)
    rules_map = await asyncio.to_thread(get_instrument_rules)
    rules = rules_map.get(symbol)
    if not rules:
        logger.error(f"[{event_id}] signal_input: REJECT — brak zasad instrument_rules w Firestore symbol={symbol}")
        return

    tick_size = str(rules.get("tickSize"))
    qty_step = str(rules.get("qtyStep"))

    # 5. Kalkulacja rozmiaru pozycji (risk_manager)
    try:
        is_long = signal.direction.upper() == "LONG"
        f_entry = round_price_by_tick(signal.entry, tick_size, "down" if is_long else "up")
        f_sl = round_price_by_tick(signal.sl, tick_size, "up" if is_long else "down")
        f_tp = round_price_by_tick(signal.tp, tick_size, "down" if is_long else "up")

        raw_qty = calculate_position_size(
            risk_per_trade_usdt=signal.risk_usdt,
            entry_price=f_entry,
            sl_price=f_sl,
            qty_step=qty_step
        )
        calculated_qty = round_qty_by_step(raw_qty, qty_step)

        if calculated_qty <= 0:
            logger.error(f"[{event_id}] signal_input: REJECT — qty wynosi 0 symbol={symbol}")
            return
    except Exception as e:
        logger.error(f"[{event_id}] signal_input: Błąd obliczeń rozmiaru symbol={symbol} error={e}")
        return

    # 6. Parametry zlecenia (Market) + stan Firestore
    order_params = {
        "symbol": symbol,
        "side": "Buy" if is_long else "Sell",
        "orderType": "Market",
        "qty": str(calculated_qty),
        "stopLoss": str(f_sl),
        "takeProfit": str(f_tp),
        "orderLinkId": event_id
    }

    state_payload = {
        "symbol": symbol,
        "status": "PLACING",
        "direction": signal.direction.upper(),
        "planned_qty": calculated_qty,
        "params": order_params,
        "event_id": event_id,
        "signal_id": signal.signal_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "planned_entry_price": f_entry,
        "planned_sl_price": f_sl,
        "planned_tp_price": f_tp
    }

    await asyncio.to_thread(state_manager.save_active_order_transactional, event_id, state_payload)
    logger.info(f"[{event_id}] state: zapisano active_order PLACING (Market) symbol={symbol}")

    # 7. Egzekucja: async place_order (HTTP w wątku w executorze)
    try:

        async def _place() -> Optional[Dict[str, str]]:
            return await executor.place_order(
                symbol=signal.symbol,
                side="Buy" if is_long else "Sell",
                qty=calculated_qty,
                order_type="Market",
                take_profit=f_tp,
                stop_loss=f_sl,
                event_id=event_id,
            )

        order_result = await async_call_with_retry(_place, event_id=event_id)
        logger.info(f"[{event_id}] place_order result: {order_result}")

        if order_result and order_result.get("orderId"):
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "PLACED", "orderId": order_result.get("orderId")},
            )
            logger.info(f"[{event_id}] execution: zlecenie Market wysłane orderId={order_result.get('orderId')} symbol={symbol}")
        else:
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "PLACEMENT_FAILED"},
            )
            logger.error(f"[{event_id}] execution: Bybit nie zwrócił orderId symbol={symbol}")

    except Exception as e:
        await asyncio.to_thread(
            state_manager.update_active_order,
            event_id,
            {"status": "ERROR", "error": str(e)},
        )
        logger.error(f"[{event_id}] execution: krytyczny błąd egzekucji symbol={symbol} error={e}")
        return

    # 8. Analytics (BigQuery) — cykl log-pnl pozostaje osobno (/log-pnl)
    analysis_data = {
        "event_id": event_id,
        "signal_id": signal.signal_id,
        "symbol": symbol,
        "timestamp": signal.timestamp,
        "direction": signal.direction.upper(),
        "entry": f_entry,
        "sl": f_sl,
        "tp": f_tp,
        "risk_pct": signal.risk_pct,
        "rr": signal.rr,
        "structure_state": signal.structure_state,
        "risk_usdt": signal.risk_usdt,
        "raw_context": signal.raw_context if isinstance(signal.raw_context, dict) else {},
        "microstructure": micro_ctx if micro_ctx else {},
        "session": getattr(signal, "session", None),
        "minute_of_day": getattr(signal, "minute_of_day", None),
        "day_of_week": getattr(signal, "day_of_week", None),
    }
    _bq_executor.submit(_log_analysis_result_bg, analysis_data)
    elapsed_ms = (perf_counter() - start_total) * 1000.0
    logger.info(f"[{event_id}] handle_immediate_signal zakończone w {elapsed_ms:.1f} ms")
    
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

                    # Aktywacja Trailing Stop po potwierdzeniu wejścia
                    try:
                        planned_sl = data.get('planned_sl_price')
                        planned_entry = data.get('planned_entry_price') or data.get('params', {}).get('price')
                        if planned_sl and planned_entry:
                            sl_distance = abs(float(planned_entry) - float(planned_sl))
                            trailing_distance = str(round(sl_distance, 4))
                            ts_result = call_with_retry(
                                executor.set_trailing_stop_for_position,
                                symbol,
                                trailing_distance
                            )
                            if ts_result:
                                state_manager.update_active_order(order_link_id, {'trailing_stop_set': True, 'trailing_distance': trailing_distance})
                                log_struct("info", "trailing_stop", "Trailing Stop aktywowany", symbol=symbol, event_id=order_link_id, distance=trailing_distance)
                            else:
                                log_struct("warning", "trailing_stop", "Trailing Stop nie ustawiony", symbol=symbol, event_id=order_link_id)
                    except Exception as e:
                        log_struct("error", "trailing_stop", "Błąd ustawiania Trailing Stop", symbol=symbol, event_id=order_link_id, error=str(e))
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