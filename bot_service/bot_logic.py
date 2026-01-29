# Lokalizacja: bot_service/bot_logic.py

import logging
import os
import time
import uuid
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext

# Ustawienie precyzji dla Decimal
getcontext().prec = 28

from shared_lib.models import AlertData
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size
from bot_service import state_manager
from bot_service.bybit_executor import BybitExecutor, BybitAPIError
from bot_service.fetch_from_firestore import load_last_processed_timestamp, save_last_processed_timestamp
from bot_service.pnl_logger_real import log_real_trade_result
# PRZENIESIONY IMPORT:
from bot_service.bigquery_logger import log_analysis_result

logger = logging.getLogger(__name__)

# =====================================================================
# === 1. NARZĘDZIA POMOCNICZE (Rounding)                             ===
# =====================================================================

def round_price_by_tick(price: float, tick_size: str, direction: str) -> float:
    """Zaokrągla cenę do najbliższego dozwolonego tick_size w kierunku 'up', 'down' lub 'none'."""
    price_decimal = Decimal(str(price))
    tick_decimal = Decimal(tick_size)
    if direction == 'down':
        quantized = (price_decimal / tick_decimal).to_integral_value(rounding=ROUND_DOWN) * tick_decimal
    elif direction == 'up':
        quantized = (price_decimal / tick_decimal).to_integral_value(rounding=ROUND_UP) * tick_decimal
    else:
        quantized = round(price_decimal / tick_decimal) * tick_decimal
    return float(quantized)

# =====================================================================
# === 2. GŁÓWNY SILNIK (PUSH)                                        ===
# =====================================================================


def handle_immediate_signal(payload: Dict[str, Any], executor: BybitExecutor):
    """
    OBSŁUGA SYGNAŁU PUSH - Sierra Chart (TRYB DRY RUN)
    """
    symbol_raw = None
    try:
        # Walidacja payloadu
        alert = AlertData.model_validate(payload)
        symbol_raw = alert.symbol
       
        # --- LOGIKA MAPOWANIA SYMBOLU ---
        base_symbol = symbol_raw.split('_')[0]
        symbol = f"{base_symbol}.P"
       
        logger.info(f"[{symbol}] PUSH: Odebrano {alert.direction} (zmapowano z {symbol_raw})")

        # 1. BLOKADA DOUBLE-TRADE
        if executor.get_open_position_side(symbol):
            logger.warning(f"[{symbol}] ODRZUCONO: Pozycja jest już otwarta.")
            return

        # 2. POBRANIE PARAMETRÓW Z FIRESTORE
        rules = get_instrument_rules().get(symbol)
        if not rules:
            logger.error(f"[{symbol}] Brak zasad handlu w Firestore dla tego symbolu!")
            return
       
        tick_size = rules["tickSize"]

        # 3. ZAOKRĄGLANIE CEN
        is_long = alert.direction.upper() == "LONG"
        final_entry = round_price_by_tick(alert.entry, tick_size, 'down' if is_long else 'up')
        final_sl = round_price_by_tick(alert.sl, tick_size, 'up' if is_long else 'down')
        final_tp = round_price_by_tick(alert.tp, tick_size, 'down' if is_long else 'up')
       

        # 4. OBLICZENIE QTY
        qty = calculate_position_size(
            risk_per_trade_usdt=alert.risk_usdt,
            entry_price=final_entry,
            sl_price=final_sl,
            qty_step=rules["qtyStep"]
        )

        if not qty or qty <= 0:
            logger.error(f"[{symbol}] Błąd obliczeń Qty.")
            return


        # 5. PRZYGOTOWANIE PARAMETRÓW ZLECENIA
        # Zmieniamy order_link_id na event_id z C++
        order_link_id = alert.event_id # Używamy event_id z payloadu
        order_params = {
            "symbol": symbol,
            "side": "Buy" if is_long else "Sell",
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(final_entry),
            "stopLoss": str(final_sl),
            "takeProfit": str(final_tp),
            "orderLinkId": order_link_id # Używamy event_id jako orderLinkId
        }

        # --- BLOKADA WYKONANIA (DRY RUN) ---
        logger.info(f"[{symbol}] ✅ DRY RUN SUCCESS! Zlecenie przygotowane: {order_params}")
       
        # Zapis do Firestore
        state_manager.save_active_order(order_link_id, {
            "symbol": symbol,
            "status": "DRY_RUN_LOG",
            "direction": alert.direction.upper(),
            "planned_qty": qty,
            "params": order_params,
            "created_at": datetime.now(timezone.utc)
        })

        # --- PRZYGOTOWANIE DANYCH DO ANALITYKI (BigQuery) ---
        analysis_data = {
            "event_id": alert.event_id,
            "signal_id": alert.signal_id,
            "symbol": alert.symbol,
            "timestamp": alert.timestamp,
            "direction": alert.direction.upper(),
            "entry": final_entry,
            "sl": final_sl,
            "tp": final_tp,
            "risk_pct": alert.risk_pct,
            "rr": alert.rr,
            "structure_state": alert.structure_state,
            "bos_high": alert.bos_high,
            "bos_low": alert.bos_low,
            "choch_up": alert.choch_up,
            "choch_down": alert.choch_down,
            "liquidity_grab_above": alert.liquidity_grab_above,
            "liquidity_grab_below": alert.liquidity_grab_below,
            "liquidity_price": alert.liquidity_price,
            "eqh_detected": alert.eqh_detected,
            "eql_detected": alert.eql_detected,
            "risk_usdt": alert.risk_usdt,
            "m2_delta": alert.m2_delta,
            "m5_rs_ratio": alert.m5_rs_ratio,
            "session": alert.session,
            "minute_of_day": alert.minute_of_day,
            "day_of_week": alert.day_of_week,
            "second": alert.second,
            "bar_range": alert.bar_range,
            "ob_range": alert.ob_range,
            "swing_range": alert.swing_range,
            "distance_to_liquidity": alert.distance_to_liquidity,
            "volatility_regime": alert.volatility_regime,
            "raw_context": alert.raw_context,

        }
        
        # Wysyłka do BigQuery
        log_analysis_result(analysis_data)

        logger.info(f"[{symbol}] ✅ ANALYTICS: Sygnał z Deltą ({alert.m2_delta}) i RS ({alert.m5_rs_ratio}) zapisany w BigQuery.")

    except Exception as e:
        logger.error(f"KRYTYCZNY BŁĄD w handle_immediate_signal: {e}", exc_info=True)

def update_filled_orders(executor: BybitExecutor):
    logger.info("[ORDER_UPDATER] Rozpoczynam cykl aktualizacji.")
    
    # --- CZĘŚĆ 1: Obsługa zleceń oczekujących na wejście (status: PLACED) ---
    placed_orders_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_orders_docs = list(state_manager.get_orders_without_status())
    orders_to_check_entry = placed_orders_docs + legacy_orders_docs

    logger.info(f"[ORDER_UPDATER] Znaleziono {len(orders_to_check_entry)} zleceń ze statusem 'PLACED' (lub bez statusu) do sprawdzenia.")

    if orders_to_check_entry:
        for order_doc in orders_to_check_entry:
            order_data, order_link_id, symbol = order_doc.to_dict(), order_doc.id, order_doc.to_dict().get('symbol')
            log_prefix = f"[{symbol}|{order_link_id}]"
            
            # <<< DODATKOWE ZABEZPIECZENIE: Sprawdzamy, czy dane w Firestore są kompletne >>>
            if not symbol:
                logger.error(f"Krytyczny błąd danych: Brak symbolu w dokumencie zlecenia {order_link_id}. Pomijam.")
                state_manager.update_active_order(order_link_id, {'status': 'ERROR_DATA_MISSING'})
                continue

            # Dodajemy licznik prób, aby uniknąć wiecznego "utknięcia"
            retry_count = order_data.get('placed_check_retries', 0)

            try:
                # <<< KLUCZOWA ZMIANA: Używamy nowej, niezawodnej funkcji, która zawsze przekazuje symbol >>>
                # UWAGA: W tym miejscu musimy upewnić się, że executor.find_order_details_by_link_id 
                # akceptuje i używa event_id jako order_link_id.
                order_details = executor.find_order_details_by_link_id(symbol=symbol, order_link_id=order_link_id)
                
                if not order_details:
                    # Logika cierpliwego czekania pozostaje, ale teraz będzie znacznie rzadziej używana
                    if retry_count < 5: # Spróbuj 5 razy (łącznie 10 minut) zanim się poddasz
                        logger.warning(f"{log_prefix} Nie można znaleźć szczegółów zlecenia PLACED w Bybit (próba {retry_count + 1}/5). Spróbuję ponownie w następnym cyklu.")
                        state_manager.update_active_order(order_link_id, {'placed_check_retries': retry_count + 1})
                    else:
                        logger.error(f"{log_prefix} Nie można znaleźć szczegółów zlecenia PLACED po 5 próbach. Ustawiam status na UNKNOWN.")
                        state_manager.update_active_order(order_link_id, {'status': 'UNKNOWN'})
                    continue # Przejdź do następnego zlecenia
                
                order_status = order_details.get('orderStatus')
                logger.info(f"{log_prefix} Status zlecenia PLACED w Bybit to: '{order_status}'.")

                if order_status == 'Filled':
                    logger.info(f"{log_prefix} Zlecenie zostało zrealizowane! Próbuję zaktualizować status na 'OPEN'.")
                    position_info = None
                    # Dajemy giełdzie chwilę na zaktualizowanie pozycji
                    for i in range(3):
                        logger.info(f"{log_prefix} Próba #{i+1} pobrania informacji o pozycji...")
                        position_info = executor.get_position_info(symbol)
                        if position_info:
                            logger.info(f"{log_prefix} Sukces! Pobrano informacje o pozycji.")
                            break
                        time.sleep(2)
                    
                    # Sprawdzamy, czy pozycja istnieje i ma ustawiony SL
                    if position_info and position_info.get('stopLoss') and float(position_info.get('stopLoss')) > 0:
                        sl_order_id = executor.find_sl_order_id(symbol, order_data)
                        logger.info(f"{log_prefix} Znaleziono pozycję i aktywny SL. Aktualizuję status w Firestore na 'OPEN' z slOrderId: {sl_order_id}.")
                        state_manager.update_active_order(order_link_id, {'status': 'OPEN', 'slOrderId': sl_order_id, 'position_opened_at': datetime.now(timezone.utc)})
                    elif position_info:
                        logger.error(f"{log_prefix} KRYTYCZNY BŁĄD: Pozycja istnieje, ale NIE MA ustawionego Stop Lossa! Uruchamiam zamknięcie awaryjne.")
                        qty, side = float(position_info.get('size', 0)), position_info.get('side')
                        if qty > 0 and executor.close_position_market(symbol, qty, side):
                            state_manager.update_active_order(order_link_id, {'status': 'CLOSED_EMERGENCY', 'reason': 'Missing SL.'})
                        else:
                            state_manager.update_active_order(order_link_id, {'status': 'ERROR_NEEDS_MANUAL_CLOSURE'})
                    else:
                        logger.error(f"{log_prefix} BŁĄD: Zlecenie zrealizowane, ale nie znaleziono otwartej pozycji w Bybit po 3 próbach. Ustawiam status na CLOSED_UNVERIFIED.")
                        state_manager.update_active_order(order_link_id, {'status': 'CLOSED_UNVERIFIED'})

                elif order_status in ['Cancelled', 'Rejected']:
                    logger.info(f"{log_prefix} Zlecenie PLACED zostało anulowane/odrzucone. Usuwam z active_orders.")
                    state_manager.delete_active_order_by_id(order_link_id)
                
                elif 'status' not in order_data:
                    state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

            except Exception as e:
                logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia PLACED: {e}", exc_info=True)

    # --- CZĘŚĆ 2: Obsługa otwartych pozycji i aktywacja TS (status: OPEN) ---
    open_orders_docs = list(state_manager.get_orders_by_status('OPEN'))
    logger.info(f"[ORDER_UPDATER] Znaleziono {len(open_orders_docs)} zleceń ze statusem 'OPEN' do zarządzania TS.")

    if not open_orders_docs: 
        logger.info("[ORDER_UPDATER] Brak otwartych pozycji do zarządzania TS. Kończę cykl.")
        return

    symbols_to_check = list({doc.to_dict().get('symbol') for doc in open_orders_docs if doc.to_dict().get('symbol')})
    if not symbols_to_check: return
        
    latest_prices = executor.get_latest_prices(symbols_to_check)
    if not latest_prices: return

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

