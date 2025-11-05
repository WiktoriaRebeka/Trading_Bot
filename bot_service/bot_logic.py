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

# Lokalizacja: bot_service/bybit_executor.py

def place_order(self, params: Dict[str, Any]) -> Optional[Dict[str, str]]:
    symbol = params.get('symbol')
    if not symbol:
        logger.error("Brak 'symbol' w parametrach zlecenia.")
        return None
    
    if params.get("orderType") == "Limit":
        has_stop_loss = params.get("stopLoss")
        has_take_profit = params.get("takeProfit")
        has_trailing_stop = params.get("trailingStop")

        # Zlecenie jest bezpieczne, jeśli ma Stop Loss ORAZ (ma Take Profit LUB ma Trailing Stop)
        if not has_stop_loss or not (has_take_profit or has_trailing_stop):
            logger.critical(
                f"[{symbol}] KRYTYCZNA PRÓBA WYSŁANIA ZLECENIA LIMIT BEZ ZABEZPIECZEŃ! Zlecenie zablokowane. Parametry: {params}"
            )
            return None

    api_symbol = symbol.replace('.P', '')
    payload = {"category": "linear", "symbol": api_symbol, "side": params['side'], "orderType": params['orderType'], "qty": str(params['qty'])}
    
    optional_params = [
        "price", "takeProfit", "stopLoss", "tpTriggerBy", "slTriggerBy", 
        "orderLinkId", "timeInForce", "trailingStop", "activePrice"
    ]
    
    for param in optional_params:
        if param in params:
            payload[param] = str(params[param])
            
    logger.info(f"[{symbol}] Wysyłanie zlecenia do Bybit: {payload}")
    try:
        result = self._send_request("POST", "/v5/order/create", params=payload)
        logger.info(f"[{symbol}] Odpowiedź Bybit na place_order: {result}")
        order_id = result.get("orderId")
        if order_id:
            return result
        logger.error(f"[{symbol}] API Bybit nie zwróciło orderId. Pełna odpowiedź 'result': {result}")
        return None
    except BybitAPIError as e:
        raise
    except Exception as e:
        logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia. Błąd: {e}", exc_info=True)
        raise
            
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
                
                position_info = None
                max_retries = 3
                retry_delay_seconds = 2

                for attempt in range(max_retries):
                    logger.info(f"{log_prefix} Próba pobrania informacji o pozycji (próba {attempt + 1}/{max_retries})...")
                    position_info = executor.get_position_info(symbol)
                    if position_info:
                        logger.info(f"{log_prefix} Sukces! Pomyślnie pobrano informacje o pozycji.")
                        break
                    
                    if attempt < max_retries - 1:
                        logger.warning(f"{log_prefix} Nie udało się pobrać informacji o pozycji. Czekam {retry_delay_seconds}s przed ponowieniem.")
                        time.sleep(retry_delay_seconds)

                if position_info:
                    # --- POCZĄTEK ZMIAN: Uproszczona logika weryfikacji ---
                    sl_set = position_info.get('stopLoss') and float(position_info.get('stopLoss')) > 0
                    
                    if sl_set:
                        logger.info(f"{log_prefix} SUKCES! Pozycja jest poprawnie zabezpieczona Stop Lossem na poziomie {position_info.get('stopLoss')}.")
                        tp_order_id, sl_order_id = executor.find_tpsl_order_ids(symbol, order_data)
                        updates = {
                            'status': 'OPEN',
                            'tpOrderId': tp_order_id,
                            'slOrderId': sl_order_id,
                            'position_opened_at': datetime.now(timezone.utc)
                        }
                        state_manager.update_active_order(order_link_id, updates)
                    else:
                        logger.critical(f"{log_prefix} KRYTYCZNY BŁĄD BEZPIECZEŃSTWA: Pozycja otwarta BEZ STOP LOSSA! Uruchamiam awaryjne zamknięcie.")
                        position_qty = float(position_info.get('size', 0))
                        position_side = position_info.get('side')

                        if position_qty > 0 and executor.close_position_market(symbol, position_qty, position_side):
                            state_manager.update_active_order(order_link_id, {'status': 'CLOSED_EMERGENCY', 'reason': 'Missing SL on position.'})
                        else:
                            state_manager.update_active_order(order_link_id, {'status': 'ERROR_NEEDS_MANUAL_CLOSURE'})
                    # --- KONIEC ZMIAN ---
                else:
                    logger.error(f"{log_prefix} KRYTYCZNY BŁĄD: Nie udało się pobrać informacji o pozycji dla {symbol} po {max_retries} próbach. Prawdopodobnie pozycja została zamknięta przed weryfikacją.")
                    state_manager.update_active_order(order_link_id, {'status': 'CLOSED_UNVERIFIED', 'reason': 'Position closed before TP/SL order IDs could be retrieved.'})

            elif order_status in ['Cancelled', 'Rejected']:
                 logger.warning(f"{log_prefix} Zlecenie otwierające zostało anulowane/odrzucone. Usuwam z aktywnych.")
                 state_manager.delete_active_order_by_id(order_link_id)
            
            elif order_status in ['New', 'PartiallyFilled']:
                logger.info(f"{log_prefix} Zlecenie wciąż aktywne (status: {order_status}). Sprawdzę ponownie.")
                if 'status' not in order_data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

        except Exception as e:
            logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia: {e}", exc_info=True)
            
def _find_matching_order(pnl_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Ulepszona, hierarchiczna funkcja dopasowująca rekord PnL z Bybit do dokumentu w active_orders.
    """
    symbol = pnl_record.get("symbol")
    pnl_order_link_id = pnl_record.get("orderLinkId")
    pnl_closing_order_id = pnl_record.get("orderId")
    log_prefix = f"[{symbol}|{pnl_closing_order_id}]"

    # --- Metoda 1: Dopasowanie po orderLinkId (najbardziej niezawodna) ---
    # Rekord PnL z Bybit zawiera orderLinkId zlecenia OTWIERAJĄCEGO.
    # Używamy go do bezpośredniego odnalezienia naszego dokumentu w Firestore.
    if pnl_order_link_id:
        logger.info(f"{log_prefix} Próba dopasowania po orderLinkId z rekordu PnL: {pnl_order_link_id}")
        # Odpytujemy bazę NA BIEŻĄCO, a nie z pamięci podręcznej
        matched_order = state_manager.get_active_order_by_id(pnl_order_link_id)
        if matched_order:
            logger.info(f"{log_prefix} ✅ MATCH FOUND (Method 1: PnL's Order Link ID).")
            return matched_order

    # --- Metoda 2: Dopasowanie po ID zlecenia zamykającego (Fallback dla TP/SL) ---
    # Użyteczna, jeśli z jakiegoś powodu orderLinkId zniknie z odpowiedzi API.
    if pnl_closing_order_id:
        logger.info(f"{log_prefix} Metoda 1 zawiodła. Próba dopasowania po ID zlecenia zamykającego (TP/SL): {pnl_closing_order_id}")
        # Odpytujemy bazę NA BIEŻĄCO
        matched_order = state_manager.get_active_order_by_tpsl_order_id(pnl_closing_order_id)
        if matched_order:
            logger.info(f"{log_prefix} ✅ MATCH FOUND (Method 2: TP/SL Order ID).")
            return matched_order

    logger.warning(f"{log_prefix} OSTATECZNIE nie znaleziono dopasowania dla rekordu PnL.")
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
                logger.info(f"[{symbol}] Pomijam już przetworzony rekord PnL o ID: {order_id_from_pnl}")
                continue

            # Wywołujemy nową, samowystarczalną funkcję dopasowującą
            active_order_data = _find_matching_order(pnl_record)
            
            if not active_order_data:
                record_ts_dt = datetime.fromtimestamp(int(pnl_record.get("updatedTime", 0)) / 1000, tz=timezone.utc)
                if current_cycle_start_time - record_ts_dt < grace_period_delta:
                    logger.warning(f"[{symbol}] Nie znaleziono dopasowania dla świeżej transakcji (zamknięta o {record_ts_dt}). Spróbuję w następnym cyklu.")
                    continue
                else:
                    logger.warning(f"[{symbol}] OSTATECZNIE nie znaleziono dopasowania dla rekordu PnL. Zostanie zalogowany jako UNMATCHED.")

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