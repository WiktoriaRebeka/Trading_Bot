# Lokalizacja: bot_service/bot_logic.py
# WERSJA FINALNA: Usunięta walidacja szerokości OB, zaktualizowana do nowego alertu, zawiera wszystkie poprawki.

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
        logger.error("Nie udało się wczytać zasad instrumentów z Firestore.")
        return

    alerts.sort(key=lambda a: a.get('received_at', datetime.min.replace(tzinfo=timezone.utc)))
    
    latest_alerts_per_symbol: Dict[str, Dict[str, Any]] = {}
    for alert_dict in alerts:
        symbol = alert_dict.get('symbol')
        if symbol: latest_alerts_per_symbol[symbol] = alert_dict

    for symbol, alert_dict in latest_alerts_per_symbol.items():
        alert_id = alert_dict.get('id', 'unknown_id')
        
        if state_manager.is_alert_processed(alert_id):
            continue

        # Krok 1: Sprawdzenie, czy już istnieje otwarta pozycja
        open_position_side = executor.get_open_position_side(symbol)
        if open_position_side and open_position_side != "ERROR":
            logger.warning(
                f"[{symbol}] ODRZUCONO NOWY ALERT. Powód: Wykryto istniejącą pozycję. "
                f"Kierunek wykrytej pozycji: {open_position_side}."
            )
            state_manager.mark_alert_as_processed(alert_id)
            continue

        # Krok 2: Anulowanie starych zleceń LIMIT
        if not executor.cancel_all_open_orders_for_symbol(symbol):
             logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się anulować poprzednich zleceń.")
             continue
        
        try:
            # Krok 3: Walidacja danych z alertu
            alert_model = AlertData.model_validate(alert_dict)

            if not _correct_and_validate_alert(alert_model):
                state_manager.mark_alert_as_processed(alert_id)
                continue

            # Krok 4: Pobranie zasad dla instrumentu
            rule = instrument_rules.get(symbol)
            if not rule or "tickSize" not in rule or "qtyStep" not in rule:
                logger.error(f"[{symbol}] Odrzucono alert: Brak zasad (tickSize, qtyStep) dla tego symbolu w Firestore.")
                state_manager.mark_alert_as_processed(alert_id)
                continue
            tick_size, qty_step = rule["tickSize"], rule["qtyStep"]

            # Krok 5: Zaokrąglenie cen i obliczenie wielkości pozycji
            is_long = alert_model.direction == 'LONG'
            final_entry = round_price_by_tick(alert_model.entry, tick_size, 'down' if is_long else 'up')
            final_sl = round_price_by_tick(alert_model.sl, tick_size, 'up' if is_long else 'down')

            final_qty = calculate_position_size(risk_per_trade_usdt=float(os.getenv("RISK_PER_TRADE_USDT", "2.5")), entry_price=final_entry, sl_price=final_sl, qty_step=qty_step)
            if not final_qty or final_qty <= 0:
                logger.error(f"[{symbol}] Odrzucono alert: Obliczona wielkość pozycji jest zerowa lub ujemna.")
                state_manager.mark_alert_as_processed(alert_id)
                continue

            # Krok 6: Przygotowanie parametrów dla Trailing Stopu
            risk_distance_1R = abs(final_entry - final_sl)
            trailing_distance_final = round_price_by_tick(risk_distance_1R * 1, tick_size, 'none')
            
            # Używamy pola 'tp' z nowego alertu jako ceny aktywacji
            activation_price_raw = alert_model.tp
            activation_price_final = round_price_by_tick(activation_price_raw, tick_size, 'down' if is_long else 'up')

            # Krok 7: Złożenie zlecenia
            order_params = {
                "symbol": symbol, "side": "Buy" if is_long else "Sell", "orderType": "Limit", 
                "qty": str(final_qty), "price": str(final_entry), "stopLoss": str(final_sl), 
                "slTriggerBy": "MarkPrice", "orderLinkId": f"bot_{alert_id.replace('-', '')[:20]}", "timeInForce": "GTC"
            }
            
            response = executor.place_order(order_params)
            if response and response.get('orderId'):
                # Krok 8: Zapisanie "złotego rekordu" do Firestore
                order_data_to_save = {
                    "symbol": symbol, "limitOrderId": response.get('orderId'), "orderLinkId": order_params["orderLinkId"],
                    "alert_id": alert_id, "direction": alert_model.direction,
                    "planned_entry_price": final_entry, "planned_sl_price": final_sl, 
                    "planned_qty": final_qty,
                    "ts_activation_price": activation_price_final,
                    "ts_distance": trailing_distance_final,
                    "ts_status": "PENDING"
                }
                state_manager.save_active_order(order_params["orderLinkId"], order_data_to_save)
                state_manager.mark_alert_as_processed(alert_id)
            else:
                logger.error(f"[{symbol}] BŁĄD: Nie udało się złożyć zlecenia wejścia.")

        except (ValidationError, BybitAPIError, Exception) as e:
            if isinstance(e, ValidationError):
                 logger.error(f"Błąd walidacji danych dla alertu ID: {alert_id}: {e}")
                 state_manager.mark_alert_as_processed(alert_id)
            elif isinstance(e, BybitAPIError):
                logger.error(f"Błąd API Bybit podczas przetwarzania alertu {alert_id}: {e}", exc_info=False)
            else:
                logger.error(f"Krytyczny błąd podczas przetwarzania alertu {alert_id}: {e}", exc_info=True)


def _correct_and_validate_alert(alert: AlertData) -> bool:
    # Sprawdzenie 1: Poprawny kierunek
    if not alert.direction or alert.direction not in ["LONG", "SHORT"]:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Brak lub nieprawidłowy kierunek.")
        return False
        
    # Sprawdzenie 2: Logiczna pozycja SL względem wejścia
    is_long_ok = (alert.direction == 'LONG' and alert.sl < alert.entry)
    is_short_ok = (alert.direction == 'SHORT' and alert.sl > alert.entry)
    if not (is_long_ok or is_short_ok):
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Nielogiczna pozycja SL. Kierunek: {alert.direction}, Wejście: {alert.entry}, SL: {alert.sl}.")
        return False
        
    # Sprawdzenie 3: Obliczenie ryzyka procentowego (tylko do logowania)
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None:
        return False

    # ================================================================= #
    # === WALIDACJA SZEROKOŚCI ORDER BLOCKA ZOSTAŁA USUNIĘTA ===
    # ================================================================= #

    logger.info(f"Alert [{alert.symbol}] przeszedł walidację. Kierunek: {alert.direction}, Ryzyko: {risk_perc}%.")
    return True


# --- POZOSTAŁE FUNKCJE (update_filled_orders, log_closed_positions_pnl, etc.) ---
# --- POZOSTAW BEZ ZMIAN, UŻYJ TYCH Z POPRZEDNICH SUGESTII, KTÓRE ZAWIERAJĄ LOGOWANIE ---
# --- PONIŻEJ WKLEJAM JE PONOWNIE DLA PEWNOŚCI ---

def update_filled_orders(executor: BybitExecutor):
    logger.info("[ORDER_UPDATER] Rozpoczynam cykl aktualizacji.")
    
    # --- CZĘŚĆ 1: Obsługa zleceń oczekujących na wejście (bez zmian) ---
    placed_orders_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_orders_docs = list(state_manager.get_orders_without_status())
    orders_to_check_entry = placed_orders_docs + legacy_orders_docs

    if orders_to_check_entry:
        for order_doc in orders_to_check_entry:
            order_data, order_link_id, symbol = order_doc.to_dict(), order_doc.id, order_doc.to_dict().get('symbol')
            log_prefix = f"[{symbol}|{order_link_id}]"
            try:
                order_details = executor.get_open_order_by_id(order_link_id) or executor.get_order_history_by_id(order_link_id)
                if not order_details:
                    state_manager.update_active_order(order_link_id, {'status': 'UNKNOWN'})
                    continue
                order_status = order_details.get('orderStatus')
                if order_status == 'Filled':
                    position_info = None
                    for _ in range(3):
                        position_info = executor.get_position_info(symbol)
                        if position_info: break
                        time.sleep(2)
                    if position_info and position_info.get('stopLoss') and float(position_info.get('stopLoss')) > 0:
                        sl_order_id = executor.find_sl_order_id(symbol, order_data)
                        state_manager.update_active_order(order_link_id, {'status': 'OPEN', 'slOrderId': sl_order_id, 'position_opened_at': datetime.now(timezone.utc)})
                    elif position_info:
                        qty, side = float(position_info.get('size', 0)), position_info.get('side')
                        if qty > 0 and executor.close_position_market(symbol, qty, side):
                            state_manager.update_active_order(order_link_id, {'status': 'CLOSED_EMERGENCY', 'reason': 'Missing SL.'})
                        else:
                            state_manager.update_active_order(order_link_id, {'status': 'ERROR_NEEDS_MANUAL_CLOSURE'})
                    else:
                        state_manager.update_active_order(order_link_id, {'status': 'CLOSED_UNVERIFIED'})
                elif order_status in ['Cancelled', 'Rejected']:
                    state_manager.delete_active_order_by_id(order_link_id)
                elif 'status' not in order_data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})
            except Exception as e:
                logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia PLACED: {e}", exc_info=True)

    # --- CZĘŚĆ 2: Obsługa otwartych pozycji i aktywacja TS ---
    open_orders_docs = list(state_manager.get_orders_by_status('OPEN'))
    if not open_orders_docs: 
        return

    symbols_to_check = list({doc.to_dict().get('symbol') for doc in open_orders_docs if doc.to_dict().get('symbol')})
    if not symbols_to_check: return
        
    latest_prices = executor.get_latest_prices(symbols_to_check)
    if not latest_prices:
        return

    for order_doc in open_orders_docs:
        try:
            order_data = order_doc.to_dict()
            order_link_id = order_doc.id
            symbol = order_data.get('symbol')
            log_prefix = f"[{symbol}|{order_link_id}]"

            if order_data.get("ts_status") != "PENDING": 
                continue

            current_price_info = latest_prices.get(symbol)
            if not current_price_info: 
                continue
            
            mark_price = float(current_price_info.get('markPrice', 0))
            activation_price = float(order_data.get("ts_activation_price", 0.0)) 
            direction = order_data.get("direction")

            logger.info(
                f"{log_prefix} Oczekuję na aktywację Trailing Stop. "
                f"Kierunek: {direction}, Aktualna cena (Mark): {mark_price}, "
                f"Cena aktywacji: {activation_price}"
            )

            if not all([mark_price > 0, activation_price > 0, direction]): 
                logger.warning(f"{log_prefix} Pomijam sprawdzanie TS z powodu niekompletnych danych (cena lub kierunek = 0/None).")
                continue

            should_activate = (direction == 'LONG' and mark_price >= activation_price) or \
                              (direction == 'SHORT' and mark_price <= activation_price)

            if should_activate:
                logger.info(f"{log_prefix} WARUNEK SPEŁNIONY! Cena ({mark_price}) osiągnęła poziom aktywacji ({activation_price}). Próbuję ustawić Trailing Stop.")
                
                if not executor.get_position_info(symbol):
                    logger.warning(f"{log_prefix} Pozycja została zamknięta przed aktywacją TS. Anuluję.")
                    state_manager.update_active_order(order_link_id, {'ts_status': 'CANCELLED'})
                    continue
                
                ts_distance = str(order_data.get("ts_distance"))
                
                logger.info(f"{log_prefix} Pozycja wciąż istnieje. Wysyłam polecenie ustawienia Trailing Stop z odległością: {ts_distance}.")
                
                if executor.set_trailing_stop_for_position(symbol, ts_distance):
                    logger.info(f"{log_prefix} SUKCES! Trailing Stop został aktywowany. Zmieniam status na 'ACTIVATED'.")
                    state_manager.update_active_order(order_link_id, {'ts_status': 'ACTIVATED'})
                else:
                    logger.error(f"{log_prefix} BŁĄD! Nie udało się ustawić Trailing Stop przez API. Status pozostaje 'PENDING', spróbuję ponownie w następnym cyklu.")
        except Exception as e:
            logger.error(f"[ORDER_UPDATER] Błąd podczas przetwarzania otwartej pozycji {order_doc.id}: {e}", exc_info=True)

def _find_matching_order(pnl_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    symbol = pnl_record.get("symbol")
    pnl_order_link_id = pnl_record.get("orderLinkId")
    pnl_closing_order_id = pnl_record.get("orderId")
    log_prefix = f"[{symbol}|{pnl_closing_order_id}]"

    if pnl_order_link_id:
        matched_order = state_manager.get_active_order_by_id(pnl_order_link_id)
        if matched_order:
            return matched_order

    if pnl_closing_order_id:
        matched_order = state_manager.get_active_order_by_sl_order_id(pnl_closing_order_id)
        if matched_order:
            return matched_order

    try:
        side = "LONG" if pnl_record.get("side") == "Buy" else "SHORT"
        qty = float(pnl_record.get("qty", 0.0))
        if all([symbol, side, qty > 0]):
            matched_order = state_manager.find_active_order_by_details(symbol, side, qty)
            if matched_order:
                return matched_order
    except (ValueError, TypeError):
        pass

    side = "LONG" if pnl_record.get("side") == "Buy" else "SHORT"
    matched_order = state_manager.get_latest_active_order_for_symbol(symbol, side)
    if matched_order:
        return matched_order

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

    processed_count = 0
    new_max_ts_dt = last_check_ts_dt

    for pnl_record in pnl_records:
        order_id_from_pnl = pnl_record.get("orderId")
        symbol = pnl_record.get("symbol")
        
        try:
            if state_manager.is_pnl_record_processed(order_id_from_pnl):
                continue

            active_order_data = _find_matching_order(pnl_record)
            
            if not active_order_data:
                record_ts_dt = datetime.fromtimestamp(int(pnl_record.get("updatedTime", 0)) / 1000, tz=timezone.utc)
                if current_cycle_start_time - record_ts_dt < grace_period_delta:
                    continue
            
            if log_real_trade_result(pnl_record, active_order_data):
                processed_count += 1
            
            record_ts_dt = datetime.fromtimestamp(int(pnl_record.get("updatedTime", 0)) / 1000, tz=timezone.utc)
            if record_ts_dt > new_max_ts_dt:
                new_max_ts_dt = record_ts_dt

        except Exception as e:
            logger.error(f"[PNL_LOGGER] Błąd podczas przetwarzania rekordu dla {symbol} [OrderID: {order_id_from_pnl}]. Błąd: {e}", exc_info=True)

    final_timestamp_to_save = max(new_max_ts_dt, current_cycle_start_time - grace_period_delta)
    save_last_processed_timestamp(final_timestamp_to_save, "pnl_logger_last_fetch_state")
    logger.info(f"[PNL_LOGGER] Zakończono cykl. Przetworzono {processed_count} rekordów.")
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

def repair_old_orders():
    logger.info("[REPAIR_SCRIPT] Uruchamiam jednorazowy skrypt naprawczy dla starych zleceň.")
    all_orders = state_manager.get_all_active_orders()
    repaired_count = 0
    for order_doc in all_orders:
        order_data = order_doc.to_dict()
        if 'status' not in order_data:
            order_id = order_doc.id
            state_manager.update_active_order(order_id, {'status': 'PLACED'})
            repaired_count += 1
    logger.info(f"[REPAIR_SCRIPT] Zakończono. Naprawiono {repaired_count} zleceń.")
    return repaired_count