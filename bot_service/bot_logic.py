# Lokalizacja: bot_service/bot_logic.py

import logging
import os
import math
import uuid
import time

from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from shared_lib.models import AlertData
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size
from bot_service import state_manager
from bot_service.pnl_logger_real import log_real_trade_result
from bot_service.bybit_executor import BybitExecutor, BybitAPIError
from bot_service.fetch_from_firestore import fetch_new_alerts_since, save_last_processed_timestamp, load_last_processed_timestamp

logger = logging.getLogger(__name__)

def process_new_alerts(executor: BybitExecutor):
    logger.info("Uruchamiam cykl przetwarzania nowych alertów.")
    last_ts = load_last_processed_timestamp("alerts_last_fetch_state")
    new_alerts, new_ts = fetch_new_alerts_since(last_ts)

    if new_alerts:
        _process_alerts_transactionally(new_alerts, executor)
        
        if new_ts and (not last_ts or new_ts > last_ts):
            save_last_processed_timestamp(new_ts, "alerts_last_fetch_state")
    else:
        logger.info("Brak nowych alertów do przetworzenia.")

def _process_alerts_transactionally(alerts: List[Dict[str, Any]], executor: BybitExecutor):
    instrument_rules = get_instrument_rules()
    if not instrument_rules:
        logger.error("Nie udało się wczytać zasad instrumentów z Firestore. Przerywam przetwarzanie alertów.")
        return

    alerts.sort(key=lambda a: a.get('received_at', datetime.min.replace(tzinfo=timezone.utc)))
    
    latest_alerts_per_symbol: Dict[str, Dict[str, Any]] = {}
    for alert_dict in alerts:
        symbol = alert_dict.get('symbol')
        if symbol:
            latest_alerts_per_symbol[symbol] = alert_dict
        else:
            alert_id = alert_dict.get('id', 'unknown_id')
            logger.warning(f"Pominięto alert {alert_id} z powodu braku symbolu.")

    for symbol, alert_dict in latest_alerts_per_symbol.items():
        alert_id = alert_dict.get('id', 'unknown_id')
        
        if state_manager.is_alert_processed(alert_id):
            logger.warning(f"[{symbol}] ODRZUCONO: Alert {alert_id} został już wcześniej przetworzony. Pomijam.")
            continue

        logger.info(f"[{symbol}] Otrzymano nowy alert. Anuluję wszystkie poprzednie, oczekujące zlecenia limit dla tego symbolu.")
        if not executor.cancel_all_open_orders_for_symbol(symbol):
             logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się anulować poprzednich zleceň. Pomijam ten symbol w cyklu, aby uniknąć ryzyka.")
             continue

        try:
            logger.info(f"--- Rozpoczynam przetwarzanie najnowszego alertu [{symbol}] ID: {alert_id} ---")
            alert_model = AlertData.model_validate(alert_dict)

            open_position_side = executor.get_open_position_side(symbol)
            if open_position_side and open_position_side != "ERROR":
                logger.warning(f"[{symbol}] ODRZUCONO: Wykryto już otwartą pozycję ({open_position_side}).")
                continue

            if not _correct_and_validate_alert(alert_model):
                logger.warning(f"[{symbol}] ODRZUCONO: Nowy alert nie przeszedł walidacji logicznej.")
                continue

            rule = instrument_rules.get(symbol)
            if not rule or "tickSize" not in rule or "qtyStep" not in rule:
                logger.warning(f"[{symbol}] ODRZUCONO (Brak Zasad): Nie znaleziono reguł dla instrumentu.")
                continue
            tick_size, qty_step = rule["tickSize"], rule["qtyStep"]

            tp_level_key = os.getenv("TAKE_PROFIT_LEVEL", "tp_3_0")
            target_tp_price = getattr(alert_model, tp_level_key, getattr(alert_model, "tp_3_0"))
            alert_tp_price_to_save = getattr(alert_model, "tp_3_0")

            if alert_model.direction == 'LONG':
                final_entry = round_price_by_tick(alert_model.entry, tick_size, 'down')
                final_sl = round_price_by_tick(alert_model.sl, tick_size, 'up')
                final_tp = round_price_by_tick(target_tp_price, tick_size, 'down')
            else: # SHORT
                final_entry = round_price_by_tick(alert_model.entry, tick_size, 'up')
                final_sl = round_price_by_tick(alert_model.sl, tick_size, 'down')
                final_tp = round_price_by_tick(target_tp_price, tick_size, 'up')

            risk_usdt = float(os.getenv("RISK_PER_TRADE_USDT", "2.5"))
            final_qty = calculate_position_size(
                risk_per_trade_usdt=risk_usdt, entry_price=final_entry,
                sl_price=final_sl, qty_step=qty_step
            )

            if not final_qty or final_qty <= 0:
                logger.warning(f"[{symbol}] ODRZUCONO (Qty=0): Obliczona wielkość pozycji wynosi zero lub jest ujemna.")
                continue

            custom_order_link_id = f"bot_{alert_id.replace('-', '')[:20]}"
            order_params = {
                "symbol": symbol, "side": "Buy" if alert_model.direction == "LONG" else "Sell",
                "orderType": "Limit", "qty": final_qty, "price": final_entry,
                "takeProfit": final_tp, "stopLoss": final_sl,
                "tpTriggerBy": "MarkPrice", "slTriggerBy": "MarkPrice",
                "orderLinkId": custom_order_link_id, "timeInForce": "GTC"
            }
            
            logger.info(f"[{symbol}] Przygotowano finalne zlecenie: {order_params}")
            response = executor.place_order(order_params)
            
            if response and response.get('orderId'):
                order_id = response.get('orderId')
                logger.info(f"[{symbol}] SUKCES! Zlecenie zintegrowane pomyślnie złożone. Order ID: {order_id}")
                
                order_data_to_save = {
                    "symbol": symbol, "limitOrderId": order_id, "orderLinkId": custom_order_link_id,
                    "alert_id": alert_id, "direction": alert_model.direction,
                    "planned_entry_price": final_entry, "planned_sl_price": final_sl, 
                    "planned_tp_price": final_tp, "planned_qty": final_qty,
                    "alert_entry_price": alert_model.entry, "alert_sl_price": alert_model.sl,
                    "alert_tp_price": alert_tp_price_to_save
                }
                state_manager.save_active_order(custom_order_link_id, order_data_to_save)
                state_manager.mark_alert_as_processed(alert_id)
            else:
                logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się złożyć zlecenia (brak orderId w odpowiedzi).")

        except ValidationError as e:
            logger.error(f"Błąd walidacji danych dla alertu ID: {alert_id}. Dane: {alert_dict}. Błąd Pydantic: {e}")
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit podczas przetwarzania alertu {alert_id}: {e}", exc_info=False)
        except Exception as e:
            logger.error(f"Krytyczny błąd podczas przetwarzania alertu {alert_id}: {e}", exc_info=True)

def update_filled_orders(executor: BybitExecutor):
    logger.info("[ORDER_UPDATER] Rozpoczynam cykl aktualizacji aktywnych zleceň.")
    
    placed_orders_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_orders_docs = list(state_manager.get_orders_without_status())
    all_orders_to_check = placed_orders_docs + legacy_orders_docs

    if not all_orders_to_check:
        logger.info("[ORDER_UPDATER] Brak zleceň oczekujących na wejście do przetworzenia.")
        return

    logger.info(f"[ORDER_UPDATER] Przetwarzam {len(all_orders_to_check)} zleceń oczekujących na wejście.")
    for order_doc in all_orders_to_check:
        order_data = order_doc.to_dict()
        order_link_id = order_doc.id
        symbol = order_data.get('symbol')
        log_prefix = f"[{symbol}|{order_link_id}]"

        try:
            order_details = executor.get_open_order_by_id(order_link_id=order_link_id)
            
            if not order_details:
                logger.info(f"{log_prefix} Zlecenie nie jest już aktywne. Sprawdzam historię...")
                order_details = executor.get_order_history_by_id(order_link_id=order_link_id)

            if not order_details:
                logger.warning(f"{log_prefix} Nie można odnaleźć zlecenia ani w aktywnych, ani w historii. Oznaczam jako 'UNKNOWN'.")
                state_manager.update_active_order(order_link_id, {'status': 'UNKNOWN'})
                continue

            order_status = order_details.get('orderStatus')

            if order_status == 'Filled':
                logger.info(f"{log_prefix} Zlecenie otwierające zrealizowane! Weryfikuję pozycję...")
                time.sleep(1) 

                position_info = executor.get_position_info(symbol)

                if position_info:
                    tp_set = position_info.get('takeProfit') and float(position_info.get('takeProfit')) > 0
                    sl_set = position_info.get('stopLoss') and float(position_info.get('stopLoss')) > 0

                    if tp_set and sl_set:
                        logger.info(f"{log_prefix} SUKCES! Pozycja ma poprawnie ustawione TP={position_info.get('takeProfit')} i SL={position_info.get('stopLoss')}.")
                        tp_order_id, sl_order_id = executor.find_tpsl_order_ids(symbol, order_data)
                        updates = {
                            'status': 'OPEN',
                            'tpOrderId': tp_order_id,
                            'slOrderId': sl_order_id,
                            'position_opened_at': datetime.now(timezone.utc)
                        }
                        state_manager.update_active_order(order_link_id, updates)
                    else:
                        logger.critical(f"{log_prefix} KRYTYCZNY BŁĄD BEZPIECZEŃSTWA: Pozycja otwarta BEZ TP/SL! Uruchamiam awaryjne zamknięcie.")
                        position_qty = float(position_info.get('size', 0))
                        position_side = position_info.get('side')

                        if position_qty > 0 and executor.close_position_market(symbol, position_qty, position_side):
                            state_manager.update_active_order(order_link_id, {'status': 'CLOSED_EMERGENCY', 'reason': 'Missing TP/SL on position.'})
                        else:
                            state_manager.update_active_order(order_link_id, {'status': 'ERROR_NEEDS_MANUAL_CLOSURE'})
                else:
                    logger.warning(f"{log_prefix} Nie udało się pobrać informacji o pozycji dla {symbol} zaraz po jej otwarciu. Spróbuję ponownie w następnym cyklu.")

            elif order_status in ['Cancelled', 'Rejected']:
                 logger.warning(f"{log_prefix} Zlecenie otwierające zostało anulowane/odrzucone. Usuwam z aktywnych.")
                 state_manager.delete_active_order_by_id(order_link_id)
            
            elif order_status in ['New', 'PartiallyFilled']:
                logger.info(f"{log_prefix} Zlecenie wciąż aktywne (status: {order_status}). Sprawdzę ponownie.")
                if 'status' not in order_data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

        except Exception as e:
            logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia: {e}", exc_info=True)

def _find_matching_order(pnl_record: Dict[str, Any], all_active_orders_by_symbol: Dict[str, List[Dict]]) -> Optional[Dict[str, Any]]:
    symbol = pnl_record.get("symbol")
    order_id_from_pnl = pnl_record.get("orderId")

    if not symbol or not order_id_from_pnl:
        return None

    orders_for_symbol = all_active_orders_by_symbol.get(symbol, [])

    for order_data in orders_for_symbol:
        if order_data.get('id') == order_id_from_pnl:
            logger.info(f"[{symbol}] MATCH FOUND (Method 0: Direct orderLinkId): PnL OrderID {order_id_from_pnl} matched document ID.")
            return order_data

    for order_data in orders_for_symbol:
        if order_data.get('tpOrderId') == order_id_from_pnl or order_data.get('slOrderId') == order_id_from_pnl:
            logger.info(f"[{symbol}] MATCH FOUND (Method 1: TP/SL OrderID): PnL OrderID {order_id_from_pnl} matched.")
            return order_data

    bybit_entry_ts_ms = int(pnl_record.get('createdTime', 0))
    if bybit_entry_ts_ms == 0:
        return None
    
    bybit_entry_dt = datetime.fromtimestamp(bybit_entry_ts_ms / 1000, tz=timezone.utc)
    time_tolerance = timedelta(minutes=2)

    for order_data in orders_for_symbol:
        firestore_entry_dt = order_data.get('created_at')
        if isinstance(firestore_entry_dt, datetime) and firestore_entry_dt.tzinfo is None:
             firestore_entry_dt = firestore_entry_dt.replace(tzinfo=timezone.utc)

        if firestore_entry_dt and abs(bybit_entry_dt - firestore_entry_dt) < time_tolerance:
            logger.info(f"[{symbol}] MATCH FOUND (Method 2: Timestamp): Bybit entry time {bybit_entry_dt} matches Firestore time {firestore_entry_dt}.")
            return order_data
            
    return None

def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania zamkniętych pozycji.")
    
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    current_cycle_start_time = datetime.now(timezone.utc)
    
    GRACE_PERIOD_MINUTES = 5
    grace_period_delta = timedelta(minutes=GRACE_PERIOD_MINUTES)
    LOOKBACK_BUFFER_HOURS = 24
    start_time_with_buffer = last_check_ts_dt - timedelta(hours=LOOKBACK_BUFFER_HOURS)
    start_time_ms = int(start_time_with_buffer.timestamp() * 1000)
    
    try:
        pnl_records = executor.get_closed_pnl_history(start_time_ms=start_time_ms)
    except Exception as e:
        logger.critical(f"[PNL_LOGGER] Krytyczny błąd podczas pobierania historii z Bybit: {e}", exc_info=True)
        return 0

    if not pnl_records:
        logger.info("[PNL_LOGGER] Nie znaleziono żadnych nowych zamkniętych pozycji.")
        save_last_processed_timestamp(current_cycle_start_time, "pnl_logger_last_fetch_state")
        return 0

    all_active_orders = state_manager.get_all_active_orders()
    all_active_orders_by_symbol: Dict[str, List[Dict]] = {}
    for doc in all_active_orders:
        order_data = doc.to_dict()
        order_data['id'] = doc.id
        symbol = order_data.get('symbol')
        if symbol:
            if symbol not in all_active_orders_by_symbol:
                all_active_orders_by_symbol[symbol] = []
            all_active_orders_by_symbol[symbol].append(order_data)

    processed_count = 0
    new_max_ts_dt = last_check_ts_dt

    for pnl_record in pnl_records:
        order_id_from_pnl = pnl_record.get("orderId")
        symbol = pnl_record.get("symbol")
        
        try:
            if state_manager.is_pnl_record_processed(order_id_from_pnl):
                logger.info(f"[{symbol}] Pomijam już przetworzony rekord PnL o ID: {order_id_from_pnl}")
                continue

            active_order_data = _find_matching_order(pnl_record, all_active_orders_by_symbol)
            
            if not active_order_data:
                record_ts_dt = datetime.fromtimestamp(int(pnl_record.get("updatedTime", 0)) / 1000, tz=timezone.utc)
                if current_cycle_start_time - record_ts_dt < grace_period_delta:
                    logger.warning(f"[{symbol}] Nie znaleziono dopasowania dla świeżej transakcji (zamknięta o {record_ts_dt}). Spróbuję w następnym cyklu.")
                    continue
                else:
                    logger.warning(f"[{symbol}] OSTATECZNIE nie znaleziono dopasowania dla rekordu PnL. Zostanie zalogowany jako UNMATCHED.")
                    active_order_data = {}

            if log_real_trade_result(pnl_record, active_order_data):
                processed_count += 1
            
            record_ts_dt = datetime.fromtimestamp(int(pnl_record.get("updatedTime", 0)) / 1000, tz=timezone.utc)
            if record_ts_dt > new_max_ts_dt:
                new_max_ts_dt = record_ts_dt

        except Exception as e:
            logger.error(f"[PNL_LOGGER] Błąd podczas przetwarzania rekordu dla {symbol} [OrderID: {order_id_from_pnl}]. Błąd: {e}", exc_info=True)

    final_timestamp_to_save = max(new_max_ts_dt, current_cycle_start_time - grace_period_delta)
    save_last_processed_timestamp(final_timestamp_to_save, "pnl_logger_last_fetch_state")
    logger.info(f"[PNL_LOGGER] Zakończono cykl. Przetworzono {processed_count} rekordów. Zaktualizowano znacznik czasu na {final_timestamp_to_save.isoformat()}.")
    return processed_count

def round_price_by_tick(price: float, tick_size: str, direction: str) -> float:
    price_decimal = Decimal(str(price))
    tick_decimal = Decimal(tick_size)
    if direction == 'down':
        quantized = (price_decimal / tick_decimal).to_integral_value(rounding=ROUND_DOWN) * tick_decimal
    elif direction == 'up':
        quantized = (price_decimal / tick_decimal).to_integral_value(rounding=ROUND_UP) * tick_decimal
    else: 
        quantized = round(price_decimal / tick_decimal) * tick_decimal
    return float(quantized)

def _calculate_risk_percentage(entry_price: float, sl_price: float) -> Optional[float]:
    if entry_price == 0: return None
    risk_distance = abs(entry_price - sl_price)
    return round((risk_distance / entry_price) * 100, 4)

def _correct_and_validate_alert(alert: AlertData) -> bool:
    if not alert.direction or alert.direction not in ["LONG", "SHORT"]:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Brak lub nieprawidłowy kierunek ('{alert.direction}'). Oryginalny directionCode: {alert.direction_code}.")
        return False
    is_long_ok = (alert.direction == 'LONG' and alert.sl < alert.entry)
    is_short_ok = (alert.direction == 'SHORT' and alert.sl > alert.entry)
    if not (is_long_ok or is_short_ok):
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Nielogiczna pozycja. Kierunek: {alert.direction}, Wejście: {alert.entry}, SL: {alert.sl}.")
        return False
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None or risk_perc < 0.43:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej minimum 0.43%. Obliczone ryzyko: {risk_perc}% (Wejście: {alert.entry}, SL: {alert.sl}).")
        return False
    logger.info(f"Alert [{alert.symbol}] przeszedł walidację. Kierunek: {alert.direction}, Ryzyko: {risk_perc}%.")
    return True

def repair_old_orders():
    """
    JEDNORAZOWY SKRYPT NAPRAWCZY.
    Przechodzi przez wszystkie dokumenty w 'active_orders' i dodaje
    pole 'status: PLACED', jeśli go brakuje.
    """
    logger.info("[REPAIR_SCRIPT] Uruchamiam jednorazowy skrypt naprawczy dla starych zleceň.")
    
    all_orders = state_manager.get_all_active_orders()
    repaired_count = 0

    for order_doc in all_orders:
        order_data = order_doc.to_dict()
        if 'status' not in order_data:
            order_id = order_doc.id
            logger.info(f"[REPAIR_SCRIPT] Naprawiam zlecenie: {order_id}, dodaję status 'PLACED'.")
            state_manager.update_active_order(order_id, {'status': 'PLACED'})
            repaired_count += 1
    
    logger.info(f"[REPAIR_SCRIPT] Zakończono. Naprawiono {repaired_count} zleceń.")
    return repaired_count