# Lokalizacja: bot_service/bot_logic.py

import logging
import os
import math
import uuid

from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from shared_lib.models import AlertData, Kline, AnalyticalCase
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size
from bot_service import state_manager
from bot_service.bigquery_logger import log_analysis_result
from bot_service.pnl_logger_real import log_real_trade_result
from bot_service.bybit_executor import BybitExecutor, BybitAPIError
from bot_service.fetch_from_firestore import fetch_new_alerts_since, save_last_processed_timestamp, load_last_processed_timestamp

logger = logging.getLogger(__name__)

def process_new_alerts(executor: BybitExecutor):
    """
    Pobiera i przetwarza nowe alerty w trybie transakcyjnym.
    Jest to główna funkcja wywoływana przez endpoint /process-alerts.
    """
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
    """
    Przetwarza listę alertów. Gwarantuje, że dla danego symbolu przetwarzany jest
    tylko najnowszy alert w cyklu, a wszystkie poprzednie zlecenia są anulowane.
    """
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
        
        # --- KROK 1: SPRAWDZENIE "WIECZNEJ PAMIĘCI" ---
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
                
                # --- KROK 2: ZAPIS DO "WIECZNEJ PAMIĘCI" ---
                state_manager.mark_alert_as_processed(alert_id)
            else:
                logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się złożyć zlecenia (brak orderId w odpowiedzi).")

        except ValidationError as e:
            logger.error(f"Błąd walidacji danych dla alertu ID: {alert_id}. Dane: {alert_dict}. Błąd Pydantic: {e}")
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit podczas przetwarzania alertu {alert_id}: {e}", exc_info=False)
        except Exception as e:
            logger.error(f"Krytyczny błąd podczas przetwarzania alertu {alert_id}: {e}", exc_info=True)

def _transform_liquidation_record(liq_record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": liq_record.get("symbol"), "orderId": f"liq_{liq_record.get('symbol')}_{liq_record.get('updatedTime')}",
        "side": "Buy" if liq_record.get("side") == "Sell" else "Sell", "qty": liq_record.get("size"),
        "avgEntryPrice": None, "avgExitPrice": liq_record.get("deliveryPrice"), "closedPnl": liq_record.get("realisedPnl"),
        "cumCommission": "0", "leverage": None, "createdTime": liq_record.get("updatedTime"),
        "updatedTime": liq_record.get("updatedTime"), "exitType": "Liquidation"
    }


def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania zamkniętych pozycji.")
    
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    current_cycle_start_time = datetime.now(timezone.utc)
    
    GRACE_PERIOD_MINUTES = 3
    grace_period_delta = timedelta(minutes=GRACE_PERIOD_MINUTES)
    LOOKBACK_BUFFER_HOURS = 12
    start_time_with_buffer = last_check_ts_dt - timedelta(hours=LOOKBACK_BUFFER_HOURS)
    logger.info(f"[PNL_LOGGER] Sprawdzam zamknięte pozycje od: {start_time_with_buffer.isoformat()} (z {LOOKBACK_BUFFER_HOURS}h buforem).")
    start_time_ms = int(start_time_with_buffer.timestamp() * 1000)
    
    all_records = []
    try:
        pnl_records = executor.get_closed_pnl_history(start_time_ms=start_time_ms)
        all_records.extend(pnl_records)
    except Exception as e:
        logger.critical(f"[PNL_LOGGER] Krytyczny błąd podczas pobierania historii z Bybit: {e}", exc_info=True)
        return 0

    if not all_records:
        logger.info("[PNL_LOGGER] Nie znaleziono żadnych nowych zamkniętych pozycji.")
        save_last_processed_timestamp(current_cycle_start_time, "pnl_logger_last_fetch_state")
        return 0

    logger.info(f"[PNL_LOGGER] Znaleziono łącznie {len(all_records)} zamkniętych pozycji. Rozpoczynam przetwarzanie.")
    pnl_records_sorted = sorted(all_records, key=lambda r: int(r.get("updatedTime", 0)))
    
    processed_count = 0
    new_max_ts_dt = last_check_ts_dt

    for pnl_record in pnl_records_sorted:
        order_id_from_pnl = pnl_record.get("orderId")
        symbol = pnl_record.get("symbol")
        exit_type = pnl_record.get("exitType")
        
        try:
            if not order_id_from_pnl or not symbol:
                continue

            logger.info(f"[PNL_LOGGER] Przetwarzanie rekordu dla {symbol} [OrderID z PnL: {order_id_from_pnl}, Typ: {exit_type}]")
            
            active_order_data = None

            # Główna metoda dopasowania
            if exit_type in ["TakeProfit", "StopLoss"]:
                active_order_data = state_manager.get_active_order_by_tpsl_order_id(order_id_from_pnl)
            
            # --- NOWA, ULEPSZONA LOGIKA FALLBACK ---
            if not active_order_data:
                logger.warning(f"[{symbol}] Nie znaleziono dopasowania po tp/sl OrderId. Uruchamiam zaawansowany fallback...")
                
                # Krok 1: Znajdź orderLinkId z historii zlecenia zamykającego
                order_history = executor.get_order_history_by_id(order_id=order_id_from_pnl)
                if order_history and order_history.get("orderLinkId"):
                    order_link_id = order_history.get("orderLinkId")
                    logger.info(f"[{symbol}] Odzyskano orderLinkId: {order_link_id} ze zlecenia zamykającego.")
                    
                    # Krok 2: Znajdź nasz "złoty rekord" po orderLinkId
                    potential_match = state_manager.get_active_order_by_id(order_link_id)
                    
                    # Krok 3: Sprawdź, czy to na pewno ten - porównaj ceny TP/SL
                    if potential_match:
                        logger.info(f"[{symbol}] Znaleziono potencjalne dopasowanie. Weryfikuję ceny TP/SL...")
                        trigger_price = float(order_history.get("triggerPrice", 0))
                        
                        is_tp_match = math.isclose(trigger_price, potential_match.get('planned_tp_price', -1))
                        is_sl_match = math.isclose(trigger_price, potential_match.get('planned_sl_price', -1))

                        if is_tp_match or is_sl_match:
                            logger.info(f"[{symbol}] Weryfikacja pomyślna. To jest prawidłowe dopasowanie.")
                            active_order_data = potential_match
                        else:
                            logger.warning(f"[{symbol}] Dopasowanie po orderLinkId nie powiodło się - ceny TP/SL się nie zgadzają.")

            if not active_order_data:
                # Logika odroczenia i UNMATCHED
                updated_time_ms = int(pnl_record.get("updatedTime", 0))
                record_ts_dt = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
                
                if current_cycle_start_time - record_ts_dt < grace_period_delta:
                    logger.warning(f"[PNL_LOGGER][ODROCZENIE] Nie znaleziono dopasowania dla świeżej transakcji. Pomijam, spróbuję w następnym cyklu.")
                    continue
                else:
                    logger.warning(f"[PNL_LOGGER] OSTATECZNIE nie znaleziono dopasowania dla orderId '{order_id_from_pnl}'.")
                    active_order_data = {}
            else:
                logger.info(f"[PNL_LOGGER] SUKCES! Znaleziono dopasowanie dla transakcji.")

            if log_real_trade_result(pnl_record, active_order_data):
                processed_count += 1
            
            updated_time_ms = int(pnl_record.get("updatedTime", 0))
            if updated_time_ms > 0:
                record_ts_dt = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
                if record_ts_dt > new_max_ts_dt:
                    new_max_ts_dt = record_ts_dt

        except Exception as e:
            logger.error(f"[PNL_LOGGER] Krytyczny błąd podczas przetwarzania rekordu dla {symbol} [OrderID: {order_id_from_pnl}]. Błąd: {e}", exc_info=True)
            continue
    
    final_timestamp_to_save = max(new_max_ts_dt, current_cycle_start_time)
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



def update_filled_orders(executor: BybitExecutor):
    """
    Cykl monitorujący zlecenia. Weryfikuje, czy zlecenie zostało zrealizowane
    i wzbogaca rekord o ID zleceń TP/SL. Posiada mechanizm awaryjnego zamykania
    pozycji, jeśli TP/SL nie zostały poprawnie ustawione przez giełdę.
    """
    logger.info("[ORDER_UPDATER] Rozpoczynam cykl aktualizacji aktywnych zleceň.")
    
    placed_orders_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_orders_docs = list(state_manager.get_orders_without_status())
    
    all_placed_orders = {doc.id: doc for doc in placed_orders_docs}
    all_placed_orders.update({doc.id: doc for doc in legacy_orders_docs})
    
    if not all_placed_orders:
        logger.info("[ORDER_UPDATER] Brak zleceň oczekujących na wejście do przetworzenia.")
        return

    logger.info(f"[ORDER_UPDATER] Przetwarzam {len(all_placed_orders)} zleceń oczekujących na wejście.")
    for order_doc in all_placed_orders.values():
        order_data = order_doc.to_dict()
        order_link_id = order_doc.id
        symbol = order_data.get('symbol')

        if not symbol or not order_link_id:
            continue

        log_prefix = f"[{symbol}|{order_link_id}]"
        logger.info(f"{log_prefix} Sprawdzam status zlecenia (status: PLACED/legacy)...")

        try:
            order_status_data = executor.get_open_order_by_id(order_link_id=order_link_id)
            
            if not order_status_data:
                logger.info(f"{log_prefix} Zlecenie nie jest już aktywne. Sprawdzam historię...")
                order_status_data = executor.get_order_history_by_id(order_link_id=order_link_id)

            if not order_status_data:
                logger.warning(f"{log_prefix} Nie można odnaleźć zlecenia. Oznaczam jako 'UNKNOWN'.")
                state_manager.update_active_order(order_link_id, {'status': 'UNKNOWN'})
                continue

            if order_status_data.get('orderStatus') == 'Filled':
                logger.info(f"{log_prefix} Zlecenie otwierające zrealizowane! Weryfikuję zlecenia TP/SL.")
                
                active_stop_orders = executor.get_active_tp_sl_orders(symbol)
                
                tp_order_id = None
                sl_order_id = None

                for stop_order in active_stop_orders:
                    trigger_price = float(stop_order.get('triggerPrice', 0))
                    if math.isclose(trigger_price, order_data.get('planned_tp_price')):
                        tp_order_id = stop_order.get('orderId')
                    elif math.isclose(trigger_price, order_data.get('planned_sl_price')):
                        sl_order_id = stop_order.get('orderId')
                
                if tp_order_id and sl_order_id:
                    updates = {
                        'status': 'OPEN',
                        'tpOrderId': tp_order_id,
                        'slOrderId': sl_order_id,
                        'position_opened_at': datetime.now(timezone.utc)
                    }
                    state_manager.update_active_order(order_link_id, updates)
                    logger.info(f"{log_prefix} SUKCES! Zlecenia TP/SL poprawnie zweryfikowane. ID: TP={tp_order_id}, SL={sl_order_id}.")
                else:
                    # --- PROCEDURA AWARYJNA ---
                    logger.critical(f"{log_prefix} KRYTYCZNY BŁĄD BEZPIECZEŃSTWA: Pozycja otwarta bez TP i/lub SL! Uruchamiam awaryjne zamknięcie pozycji.")
                    
                    position_qty = float(order_status_data.get('cumExecQty', 0))
                    position_side = order_status_data.get('side') # 'Buy' lub 'Sell'

                    if executor.close_position_market(symbol, position_qty, position_side):
                        logger.info(f"{log_prefix} Pozycja została awaryjnie zamknięta zleceniem MARKET.")
                        state_manager.update_active_order(order_link_id, {'status': 'CLOSED_EMERGENCY', 'reason': 'Missing TP/SL on exchange.'})
                    else:
                        logger.critical(f"{log_prefix} KRYTYCZNY BŁĄD SYSTEMOWY: Nie udało się awaryjnie zamknąć pozycji! Wymagana natychmiastowa interwencja manualna!")
                        state_manager.update_active_order(order_link_id, {'status': 'ERROR_NEEDS_MANUAL_CLOSURE'})

            elif order_status_data.get('orderStatus') in ['Cancelled', 'Rejected']:
                 logger.warning(f"{log_prefix} Zlecenie otwierające zostało anulowane/odrzucone. Oznaczam jako anulowane.")
                 state_manager.update_active_order(order_link_id, {'status': 'CANCELLED'})
            
            elif order_status_data.get('orderStatus') in ['New', 'PartiallyFilled']:
                logger.info(f"{log_prefix} Zlecenie wciąż aktywne (status: {order_status_data.get('orderStatus')}). Sprawdzę ponownie.")
                if 'status' not in order_data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

        except Exception as e:
            logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia PLACED: {e}", exc_info=True)


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