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
    return {
        "symbol": liq_record.get("symbol"), "orderId": f"liq_{liq_record.get('symbol')}_{liq_record.get('updatedTime')}",
        "side": "Buy" if liq_record.get("side") == "Sell" else "Sell", "qty": liq_record.get("size"),
        "avgEntryPrice": None, "avgExitPrice": liq_record.get("deliveryPrice"), "closedPnl": liq_record.get("realisedPnl"),
        "cumCommission": "0", "leverage": None, "createdTime": liq_record.get("updatedTime"),
        "updatedTime": liq_record.get("updatedTime"), "exitType": "Liquidation"
    }


# Lokalizacja: bot_service/pnl_logger_real.py
# ZASTĄP FUNKCJĘ 'log_real_trade_result' PONIŻSZĄ WERSJĄ

def log_real_trade_result(pnl_data: Dict[str, Any], active_order_data: Dict[str, Any]) -> bool:
    order_id = pnl_data.get("orderId", f"unknown_{int(datetime.now().timestamp())}")
    symbol = pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return False

    if not acquire_lock_for_order(order_id):
        return False

    is_matched = bool(active_order_data and 'alert_id' in active_order_data)
    alert_id = active_order_data.get('alert_id', 'UNMATCHED_OR_MANUAL')
    
    if not is_matched:
        logger.warning(f"{log_prefix} Nie znaleziono dopasowania. Transakcja zostanie zapisana jako UNMATCHED.")
    else:
        logger.info(f"{log_prefix} Rozpoczynam transakcyjny zapis (alert_id: {alert_id}).")

    try:
        # --- Konwersja na Decimal dla precyzji ---
        qty = Decimal(pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(pnl_data.get("avgExitPrice", "0.0"))
        net_pnl = Decimal(pnl_data.get("closedPnl") or "0.0")
        commission = Decimal(pnl_data.get("cumCommission") or "0.0")
        
        # --- Obliczenia wymaganych pól ---
        entry_value_usdt = qty * avg_entry_price
        exit_value_usdt = qty * avg_exit_price
        gross_pnl_usdt = net_pnl + commission

        planned_risk_usdt = None
        realized_rrr = None
        exit_price_result = None

        if is_matched:
            planned_sl_price = active_order_data.get("planned_sl_price")
            planned_sl_price_dec = Decimal(str(planned_sl_price)) if planned_sl_price is not None else Decimal("0.0")

            if planned_sl_price_dec > 0 and avg_entry_price > 0:
                risk_per_unit = abs(avg_entry_price - planned_sl_price_dec)
                planned_risk_usdt_dec = risk_per_unit * qty
                
                if planned_risk_usdt_dec > 0:
                    realized_rrr_dec = (net_pnl / planned_risk_usdt_dec)
                    planned_risk_usdt = float(planned_risk_usdt_dec)
                    realized_rrr = float(realized_rrr_dec)
            
            # Ustalenie exit_price_result
            exit_type = pnl_data.get("exitType")
            if exit_type == "TakeProfit":
                exit_price_result = active_order_data.get("planned_tp_price")
            elif exit_type == "StopLoss":
                exit_price_result = active_order_data.get("planned_sl_price")

        # --- Budowa finalnego obiektu do zapisu ---
        transformed_data = {
            "alert_id": alert_id,
            "order_id": order_id,
            "symbol": symbol,
            "direction": active_order_data.get("direction", pnl_data.get("side")),
            "qty": float(qty),
            "leverage": int(float(pnl_data.get("leverage", 0))) or None,
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(avg_exit_price),
            "entry_value_usdt": float(entry_value_usdt),
            "exit_value_usdt": float(exit_value_usdt),
            "gross_pnl_usdt": float(gross_pnl_usdt),
            "commission_usdt": float(commission),
            "net_pnl_usdt": float(net_pnl),
            "exit_type": pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "planned_risk_usdt": planned_risk_usdt,
            "realized_rrr": realized_rrr,
            "alert_entry_price": active_order_data.get("alert_entry_price"), # <-- POPRAWIONE NAZWY
            "alert_sl_price": active_order_data.get("alert_sl_price"),
            "alert_tp_price": active_order_data.get("alert_tp_price"),
            "planned_entry_price": active_order_data.get("planned_entry_price"),
            "planned_sl_price": active_order_data.get("planned_sl_price"),
            "planned_tp_price": active_order_data.get("planned_tp_price"),
            "exit_price_result": exit_price_result,
            "tp_price_chart": active_order_data.get("alert_tp_price"), # Zgodnie ze schematem, to ma być cena z alertu TV
        }
    except Exception as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return False

    try:
        client = bigquery_logger.get_bigquery_client()
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            logger.info(f"{log_prefix} SUKCES! Pomyślnie zapisano wynik transakcji do BigQuery.")
            
            if is_matched:
                order_link_id_to_delete = active_order_data.get('id')
                if order_link_id_to_delete:
                    logger.info(f"{log_prefix} Sprzątanie: Usuwanie dokumentu '{order_link_id_to_delete}' z kolekcji active_orders.")
                    state_manager.delete_active_order_by_id(order_link_id_to_delete)
                else:
                    logger.error(f"{log_prefix} BŁĄD KRYTYCZNY: Nie można usunąć rekordu, ponieważ 'id' (orderLinkId) nie zostało znalezione w dopasowanych danych.")
            
            return True
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania wierszy do BigQuery: {errors}. Dokument w active_orders NIE został usunięty.")
            return False
    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}. Dokument w active_orders NIE został usunięty.", exc_info=True)
        return False

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