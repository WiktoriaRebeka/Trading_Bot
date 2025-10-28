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

    # Sortujemy alerty od najstarszego do najnowszego
    alerts.sort(key=lambda a: a.get('received_at', datetime.min.replace(tzinfo=timezone.utc)))
    
    # Grupujemy alerty po symbolu, aby łatwo znaleźć najnowszy
    latest_alerts_per_symbol: Dict[str, Dict[str, Any]] = {}
    for alert_dict in alerts:
        try:
            # Szybka walidacja Pydantic, aby uzyskać symbol
            symbol = alert_dict.get('symbol')
            if symbol:
                latest_alerts_per_symbol[symbol] = alert_dict
        except Exception:
            alert_id = alert_dict.get('id', 'unknown_id')
            logger.warning(f"Pominięto alert {alert_id} z powodu braku symbolu lub błędu parsowania wstępnego.")

    # --- NOWA, POPRAWIONA LOGIKA ---
    # Przechodzimy po unikalnych symbolach, dla których otrzymaliśmy alerty w tym cyklu
    for symbol, alert_dict in latest_alerts_per_symbol.items():
        alert_id = alert_dict.get('id', 'unknown_id')
        
        # KROK 1: ZAWSZE ANULUJ STARE ZLECENIA
        # Robimy to na samym początku dla każdego symbolu, który otrzymał nowy alert.
        logger.info(f"[{symbol}] Otrzymano nowy alert. Anuluję wszystkie poprzednie, oczekujące zlecenia limit dla tego symbolu.")
        if not executor.cancel_all_open_orders_for_symbol(symbol):
             logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się anulować poprzednich zleceň. Pomijam ten symbol w cyklu, aby uniknąć ryzyka.")
             continue # Przejdź do następnego symbolu

        try:
            # KROK 2: PRZETWÓRZ NAJNOWSZY ALERT
            logger.info(f"--- Rozpoczynam przetwarzanie najnowszego alertu [{symbol}] ID: {alert_id} ---")
            alert_model = AlertData.model_validate(alert_dict)

            # Sprawdzamy, czy nie ma już otwartej pozycji
            open_position_side = executor.get_open_position_side(symbol)
            if open_position_side and open_position_side != "ERROR":
                logger.warning(f"[{symbol}] ODRZUCONO: Wykryto już otwartą pozycję ({open_position_side}).")
                continue

            # Walidacja logiki alertu
            if not _correct_and_validate_alert(alert_model):
                logger.warning(f"[{symbol}] ODRZUCONO: Nowy alert nie przeszedł walidacji logicznej.")
                continue

            # Reszta logiki pozostaje taka sama...
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
            else:
                logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się złożyć zlecenia (brak orderId w odpowiedzi).")

        except ValidationError as e:
            logger.error(f"Błąd walidacji danych dla alertu ID: {alert_id}. Dane: {alert_dict}. Błąd Pydantic: {e}")
            continue
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit podczas przetwarzania alertu {alert_id}: {e}", exc_info=False)
        except Exception as e:
            logger.error(f"Krytyczny błąd podczas przetwarzania alertu {alert_id}: {e}", exc_info=True)


def _transform_liquidation_record(liq_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transformuje rekord likwidacji z API Bybit do formatu zbliżonego
    do rekordu PnL, aby można go było przetworzyć w tej samej logice.
    """
    return {
        "symbol": liq_record.get("symbol"),
        "orderId": f"liq_{liq_record.get('symbol')}_{liq_record.get('updatedTime')}",
        "side": "Buy" if liq_record.get("side") == "Sell" else "Sell", # Strona zamykająca pozycję
        "qty": liq_record.get("size"),
        "avgEntryPrice": None, # Tego nie mamy w danych o likwidacji
        "avgExitPrice": liq_record.get("deliveryPrice"),
        "closedPnl": liq_record.get("realisedPnl"),
        "cumCommission": "0", # Prowizja jest już wliczona w realisedPnl
        "leverage": None,
        "createdTime": liq_record.get("updatedTime"), # Używamy czasu likwidacji jako obu
        "updatedTime": liq_record.get("updatedTime"),
        "exitType": "Liquidation"
    }


# Lokalizacja: bot_service/bot_logic.py

def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    """
    Pobiera zamknięte pozycje, znajduje dla nich dopasowanie w `active_orders`
    i loguje wzbogacony rekord do BigQuery.
    WERSJA FINALNA: Używa nowej, niezawodnej metody dopasowania po tpOrderId/slOrderId
    oraz obsługuje likwidacje i inne przypadki brzegowe.
    """
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
            
            # --- NOWA, UPROSZCZONA LOGIKA DOPASOWYWANIA ---
            
            # KROK 1: Zawsze próbuj znaleźć dopasowanie po orderId z rekordu PnL w naszych polach tpOrderId/slOrderId.
            # To jest najbardziej niezawodna metoda.
            active_order_data = state_manager.get_active_order_by_tpsl_order_id(order_id_from_pnl)
            
            # KROK 2: Jeśli to zawiedzie, spróbuj metody fallback opartej na orderLinkId.
            # Ta metoda jest mniej pewna, ale może pomóc w przypadkach brzegowych.
            if not active_order_data:
                logger.warning(f"[{symbol}] Nie znaleziono dopasowania po tp/sl OrderId. Próbuję metody fallback po orderLinkId...")
                
                order_link_id_from_pnl = pnl_record.get("orderLinkId")
                if order_link_id_from_pnl and order_link_id_from_pnl.startswith("bot_"):
                    active_order_data = state_manager.get_active_order_by_id(order_link_id_from_pnl)
                else:
                    # Ta część jest mało prawdopodobna, ale zostawiamy jako ostateczność
                    order_history = executor.get_order_history_by_id(order_id=order_id_from_pnl)
                    if order_history and order_history.get("orderLinkId", "").startswith("bot_"):
                        order_link_id = order_history.get("orderLinkId")
                        active_order_data = state_manager.get_active_order_by_id(order_link_id)

            # --- KONIEC NOWEJ LOGIKI ---

            if not active_order_data:
                updated_time_ms = int(pnl_record.get("updatedTime", 0))
                record_ts_dt = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
                
                if current_cycle_start_time - record_ts_dt < grace_period_delta:
                    logger.warning(f"[PNL_LOGGER][ODROCZENIE] Nie znaleziono dopasowania dla świeżej transakcji. Pomijam, spróbuję w następnym cyklu.")
                    continue
                else:
                    logger.warning(f"[PNL_LOGGER] OSTATECZNIE nie znaleziono dopasowania dla orderId '{order_id_from_pnl}'.")
                    active_order_data = {} # Przekaż pusty słownik, aby zalogować jako UNMATCHED
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
    Cykl monitorujący zlecenia.
    1. Dla statusu 'PLACED' (i starych bez statusu): Sprawdza, czy zlecenie zostało zrealizowane i wzbogaca o ID TP/SL.
    2. Dla statusu 'OPEN': Sprawdza, czy można aktywować zaawansowany Trailing Stop.
    """
    logger.info("[ORDER_UPDATER] Rozpoczynam cykl aktualizacji aktywnych zleceň.")
    
    # --- CZĘŚĆ 1: Obsługa zleceń oczekujących na wejście ---
    placed_orders_docs = list(state_manager.get_orders_by_status('PLACED'))
    legacy_orders_docs = list(state_manager.get_orders_without_status())
    
    all_placed_orders = {doc.id: doc for doc in placed_orders_docs}
    all_placed_orders.update({doc.id: doc for doc in legacy_orders_docs})
    
    if all_placed_orders:
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
                    logger.info(f"{log_prefix} Zlecenie otwierające zrealizowane! Szukam powiązanych zleceň TP/SL.")
                    
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
                        logger.info(f"{log_prefix} SUKCES! Zaktualizowano rekord o ID zleceň TP: {tp_order_id} i SL: {sl_order_id}.")
                    else:
                        logger.warning(f"{log_prefix} Zlecenie zrealizowane, ale nie znaleziono pasujących zleceň TP/SL na giełdzie. Spróbuję ponownie.")

                elif order_status_data.get('orderStatus') in ['Cancelled', 'Rejected']:
                     logger.warning(f"{log_prefix} Zlecenie otwierające zostało anulowane/odrzucone. Oznaczam jako anulowane.")
                     state_manager.update_active_order(order_link_id, {'status': 'CANCELLED'})
                
                elif order_status_data.get('orderStatus') in ['New', 'PartiallyFilled']:
                    logger.info(f"{log_prefix} Zlecenie wciąż aktywne (status: {order_status_data.get('orderStatus')}). Sprawdzę ponownie.")
                    if 'status' not in order_data:
                        state_manager.update_active_order(order_link_id, {'status': 'PLACED'})

            except Exception as e:
                logger.error(f"{log_prefix} Błąd podczas aktualizacji zlecenia PLACED: {e}", exc_info=True)

    # --- CZĘŚĆ 2: Obsługa otwartych pozycji (Trailing Stop) ---
    logger.info("[ORDER_UPDATER] Sprawdzam otwarte pozycje pod kątem aktywacji Trailing Stop.")
    open_orders_docs = state_manager.get_orders_by_status('OPEN')

    for order_doc in open_orders_docs:
        order_data = order_doc.to_dict()
        order_link_id = order_doc.id
        symbol = order_data.get('symbol')

        if order_data.get('trailing_stop_activated'):
            continue

        log_prefix = f"[{symbol}|{order_link_id}]"
        
        try:
            mark_price = executor.get_mark_price(symbol)
            if not mark_price:
                logger.warning(f"{log_prefix} Nie udało się pobrać ceny rynkowej dla TSL. Pomijam.")
                continue

            direction = order_data.get('direction')
            alert_id = order_data.get('alert_id')
            
            alert_data = state_manager.get_alert_data_by_id(alert_id)
            if not alert_data:
                logger.warning(f"{log_prefix} Nie udało się pobrać danych alertu {alert_id} dla TSL. Pomijam.")
                continue
            
            alert_model = AlertData.model_validate(alert_data)
            
            tsl_activation_price = alert_model.tp_5_0
            tsl_floor_price = alert_model.tp_4_0
            
            should_activate_tsl = False
            if direction == 'LONG' and mark_price > tsl_activation_price:
                should_activate_tsl = True
            elif direction == 'SHORT' and mark_price < tsl_activation_price:
                should_activate_tsl = True

            if should_activate_tsl:
                logger.info(f"{log_prefix} CENA RYNKOWA ({mark_price}) PRZEKROCZYŁA PRÓG AKTYWACJI TSL ({tsl_activation_price}).")
                
                tp_order_id = order_data.get('tpOrderId')
                
                if executor.cancel_order(symbol, order_id=tp_order_id):
                    logger.info(f"{log_prefix} Stare zlecenie Take Profit ({tp_order_id}) anulowane.")
                    
                    # Odległość TSL = 2R (różnica między tp_4_0 a tp_2_0)
                    trailing_distance = abs(tsl_floor_price - alert_model.tp_2_0)
                    
                    if executor.set_trailing_stop(symbol, trailing_stop_price=str(trailing_distance), sl_price=str(tsl_floor_price)):
                        logger.info(f"{log_prefix} SUKCES! Trailing Stop aktywowany. Podłoga zysku: {tsl_floor_price}, odległość śledzenia: {trailing_distance}.")
                        state_manager.update_active_order(order_link_id, {'trailing_stop_activated': True, 'status': 'TRAILING'})
                    else:
                        logger.error(f"{log_prefix} Nie udało się ustawić Trailing Stop. Pozycja pozostaje bez TP!")
                else:
                    logger.error(f"{log_prefix} Nie udało się anulować starego zlecenia Take Profit.")

        except Exception as e:
            logger.error(f"{log_prefix} Błąd podczas sprawdzania Trailing Stop: {e}", exc_info=True)


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