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
from bot_service.bybit_executor import BybitExecutor, BybitAPIError # <-- WAŻNE: Dodaj import BybitAPIError
from bot_service.fetch_from_firestore import fetch_new_alerts_since, save_last_processed_timestamp, load_last_processed_timestamp

logger = logging.getLogger(__name__)


def run_combined_cycle(executor: BybitExecutor):
    """Główna, połączona pętla logiki z zapewnioną atomowością."""
    logger.info("Uruchamiam połączony cykl analityczno-transakcyjny.")
    
    last_ts = load_last_processed_timestamp("main_cycle_last_fetch_state")
    new_alerts, new_ts = fetch_new_alerts_since(last_ts)

    if new_alerts:
        # Zamiast dwóch oddzielnych funkcji, mamy jedną, która przetwarza alerty atomowo
        process_alerts_atomically(new_alerts, executor)
        
        if new_ts and (not last_ts or new_ts > last_ts):
            save_last_processed_timestamp(new_ts, "main_cycle_last_fetch_state")
    
    _run_analysis_of_existing_cases()
    logger.info("Zakończono połączony cykl analityczno-transakcyjny.")

def round_price_by_tick(price: float, tick_size: str, direction: str) -> float:
    """
    Zaokrągla cenę do najbliższego kroku (ticka) w dół ('down') lub w górę ('up').
    Używa Decimal dla precyzji.
    """
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
    """Oblicza procentową odległość SL od ceny wejścia."""
    if entry_price == 0:
        return None
    risk_distance = abs(entry_price - sl_price)
    return round((risk_distance / entry_price) * 100, 4)


def _correct_and_validate_alert(alert: AlertData) -> bool:
    """
    Waliduje logikę biznesową alertu. Zwraca True, jeśli jest poprawny, False w przeciwnym razie.
    """
    if not alert.direction or alert.direction not in ["LONG", "SHORT"]:
        logger.warning(
            f"Odrzucono alert [{alert.symbol}]: Brak lub nieprawidłowy kierunek ('{alert.direction}'). "
            f"Oryginalny directionCode: {alert.direction_code}."
        )
        return False
    is_long_ok = (alert.direction == 'LONG' and alert.sl < alert.entry)
    is_short_ok = (alert.direction == 'SHORT' and alert.sl > alert.entry)

    if not (is_long_ok or is_short_ok):
        logger.warning(
            f"Odrzucono alert [{alert.symbol}]: Nielogiczna pozycja. "
            f"Kierunek: {alert.direction}, Wejście: {alert.entry}, SL: {alert.sl}."
        )
        return False
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None or risk_perc < 0.43:
        logger.warning(
            f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej minimum 0.43%. "
            f"Obliczone ryzyko: {risk_perc}% (Wejście: {alert.entry}, SL: {alert.sl})."
        )
        return False
    
    logger.info(
        f"Alert [{alert.symbol}] przeszedł walidację. "
        f"Kierunek: {alert.direction}, Ryzyko: {risk_perc}%."
    )
    return True

def process_alerts_atomically(alerts: List[Dict[str, Any]], executor: BybitExecutor):
    instrument_rules = get_instrument_rules()
    if not instrument_rules:
        logger.error("Nie udało się wczytać zasad instrumentów z Firestore. Przerywam przetwarzanie alertów.")
        return

    processed_symbols_in_cycle = set()

    for alert_dict in alerts:
        alert_id = alert_dict.get('id', 'unknown')
        alert_model = None
        symbol = alert_dict.get("symbol", "UNKNOWN")

        try:
            alert_model = AlertData.model_validate(alert_dict)
            symbol = alert_model.symbol
            logger.info(f"--- Rozpoczynam przetwarzanie alertu [{symbol}] ID: {alert_id} ---")

            if symbol in processed_symbols_in_cycle:
                logger.info(f"[{symbol}] Pomijam (symbol już przetworzony w tym cyklu).")
                continue

            ### POCZĄTEK POPRAWKI ###
            # Krok 1: Weryfikacja stanu rynku PRZED podjęciem jakichkolwiek działań.
            open_position_side = executor.get_open_position_side(symbol)
            
            if open_position_side and open_position_side != "ERROR":
                # SCENARIUSZ A: Pozycja już istnieje.
                logger.info(f"[{symbol}] Wykryto otwartą pozycję: {open_position_side}.")
                # Absolutnie NIE WOLNO anulować żadnych zleceń, aby chronić istniejący SL/TP.
                if alert_model.direction != open_position_side:
                    logger.warning(f"[{symbol}] ODRZUCONO (Strażnik Pozycji): Nowy alert ({alert_model.direction}) jest przeciwny do otwartej pozycji.")
                    processed_symbols_in_cycle.add(symbol)
                    continue
                else:
                    logger.info(f"[{symbol}] Nowy alert jest zgodny z otwartą pozycją. Kontynuuję przetwarzanie bez anulowania zleceń.")
            else:
                # SCENARIUSZ B: Rynek jest czysty (brak otwartej pozycji).
                # TYLKO W TYM PRZYPADKU możemy bezpiecznie posprzątać "osierocone" zlecenia.
                logger.info(f"[{symbol}] Brak otwartej pozycji. Anuluję wszystkie oczekujące zlecenia limit dla tego symbolu.")
                executor.cancel_all_open_orders_for_symbol(symbol)
            ### KONIEC POPRAWKI ###

            if not _correct_and_validate_alert(alert_model):
                logger.warning(f"[{symbol}] ODRZUCONO (Walidacja Logiczna).")
                processed_symbols_in_cycle.add(symbol)
                continue
            
            logger.info(f"[{symbol}] Alert przeszedł wszystkie filtry transakcyjne.")

            rule = instrument_rules.get(symbol)
            if not rule or "tickSize" not in rule or "qtyStep" not in rule:
                logger.warning(f"[{symbol}] ODRZUCONO (Brak Zasad): Nie znaleziono reguł instrumentu w Firestore.")
                processed_symbols_in_cycle.add(symbol)
                continue
            
            tick_size, qty_step = rule["tickSize"], rule["qtyStep"]

            if alert_model.direction == 'LONG':
                final_entry = round_price_by_tick(alert_model.entry, tick_size, 'up')
                final_sl = round_price_by_tick(alert_model.sl, tick_size, 'down')
                final_tp = round_price_by_tick(alert_model.tp_2_0, tick_size, 'up')
            else: # SHORT
                final_entry = round_price_by_tick(alert_model.entry, tick_size, 'down')
                final_sl = round_price_by_tick(alert_model.sl, tick_size, 'up')
                final_tp = round_price_by_tick(alert_model.tp_2_0, tick_size, 'down')

            risk_usdt = float(os.getenv("RISK_PER_TRADE_USDT", "2.5"))
            final_qty = calculate_position_size(
                risk_per_trade_usdt=risk_usdt, entry_price=final_entry,
                sl_price=final_sl, qty_step=qty_step
            )

            if not final_qty or final_qty <= 0:
                logger.warning(f"[{symbol}] ODRZUCONO (Qty=0): Obliczona wielkość pozycji wynosi zero.")
                processed_symbols_in_cycle.add(symbol)
                continue

            logger.info(f"[{symbol}] Tworzenie teczek analitycznych jest obecnie wyłączone. Przechodzę do trybu transakcyjnego.")

            logger.info(
                f"[{symbol}] Przygotowano zlecenie: Entry={final_entry}, SL={final_sl}, "
                f"TP={final_tp}, Qty={final_qty}."
            )
            order_params = {
                "symbol": symbol, "side": "Buy" if alert_model.direction == "LONG" else "Sell",
                "orderType": "Limit", "qty": final_qty, "price": final_entry,
                "takeProfit": final_tp, "stopLoss": final_sl,
                "orderLinkId": f"bracket_{alert_id.replace('-', '')[:12]}_{int(datetime.now().timestamp())}",
                "timeInForce": "GTC"
            }

            response = executor.place_order(order_params)
            
            if response:
                logger.info(f"[{symbol}] Zlecenie zintegrowane pomyślnie złożone. Order ID: {response.get('orderId')}")
                order_data_to_save = {
                    "symbol": symbol, "orderId": response.get("orderId"), "status": "NEW_BRACKET",
                    "alert_id": alert_id, "direction": alert_model.direction,
                    "final_entry_price": final_entry, "final_sl_price": final_sl, 
                    "final_tp_price": final_tp, "tp_price_chart": alert_model.tp_3_0
                }
                state_manager.save_active_order(response.get("orderId"), order_data_to_save)
            else:
                raise Exception("Nie udało się złożyć zlecenia zintegrowanego (brak odpowiedzi od Bybit).")

            processed_symbols_in_cycle.add(symbol)

        except BybitAPIError as e:
            if e.ret_code == 110093:
                logger.warning(f"[{symbol}] Zlecenie odrzucone (110093) z powodu ustawień margin. Pomijam.")
            else:
                logger.error(f"Błąd API Bybit dla alertu {alert_id}: {e}", exc_info=False)
            processed_symbols_in_cycle.add(symbol)
        except Exception as e:
            logger.error(f"Krytyczny błąd podczas atomowego przetwarzania alertu {alert_id}: {e}", exc_info=True)
            processed_symbols_in_cycle.add(symbol)


def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania PnL.")
    
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    if not last_check_ts_dt:
        logger.error("[PNL_LOGGER] Nie udało się załadować ostatniego znacznika czasu. Przerywam cykl, aby uniknąć duplikatów.")
        return 0

    ### POCZĄTEK POPRAWKI ###
    # Krok 1: Ustal "punkt kontrolny" dla bieżącego cyklu PRZED zapytaniem do API.
    # To jest czas, do którego będziemy sprawdzać i który zapiszemy na koniec.
    current_cycle_timestamp = datetime.now(timezone.utc)
    logger.info(f"[PNL_LOGGER] Sprawdzam zamknięte pozycje w przedziale od {last_check_ts_dt.isoformat()} do {current_cycle_timestamp.isoformat()}")
    
    start_time_ms = int(last_check_ts_dt.timestamp() * 1000)
    # Dodajemy endTimeMs, aby mieć pewność, że nie pobierzemy transakcji, które zamknęły się w trakcie działania tej funkcji.
    end_time_ms = int(current_cycle_timestamp.timestamp() * 1000)

    pnl_records = sorted(
        executor.get_closed_pnl_history(start_time_ms=start_time_ms, end_time_ms=end_time_ms),
        key=lambda r: int(r.get("updatedTime", 0))
    )
    
    if not pnl_records:
        logger.info("[PNL_LOGGER] Nie znaleziono nowych zamkniętych pozycji na Bybit w tym przedziale czasowym.")
        # Krok 2: Mimo braku rekordów, MUSIMY zaktualizować znacznik czasu.
        save_last_processed_timestamp(current_cycle_timestamp, "pnl_logger_last_fetch_state")
        logger.info(f"[PNL_LOGGER] Zaktualizowano znacznik czasu na {current_cycle_timestamp.isoformat()}.")
        return 0

    logger.info(f"[PNL_LOGGER] Znaleziono {len(pnl_records)} zamkniętych pozycji na Bybit. Rozpoczynam przetwarzanie.")
    processed_count = 0
    
    for pnl_record in pnl_records:
        symbol = pnl_record.get("symbol")
        order_id_from_db = None
        
        try:
            if not symbol:
                logger.warning("[PNL_LOGGER] Pominięto rekord PnL bez symbolu.", extra={"json_fields": {"pnl_record": pnl_record}})
                continue

            active_order_data = state_manager.get_active_order_by_symbol(symbol)
            
            if active_order_data:
                logger.info(f"[PNL_LOGGER] Znaleziono dopasowanie dla {symbol} w Firestore.")
                alert_id = active_order_data.get('alert_id', 'MATCHED_NO_ALERT_ID')
                order_id_from_db = active_order_data.get('orderId')
            else:
                logger.warning(f"[PNL_LOGGER] Nie znaleziono dopasowania dla {symbol}. Transakcja zostanie zalogowana jako 'UNMATCHED'.")
                active_order_data = {}
                alert_id = 'UNMATCHED_OR_MANUAL'

            enriched_pnl_data = pnl_record.copy()
            enriched_pnl_data['alert_id'] = alert_id
            
            log_real_trade_result(enriched_pnl_data, active_order_data)
            processed_count += 1
            
            if order_id_from_db:
                state_manager.delete_active_order_by_id(order_id_from_db)

        except Exception as e:
            logger.error(f"[PNL_LOGGER] Krytyczny błąd podczas przetwarzania rekordu PnL dla symbolu {symbol}. Rekord zostanie pominięty. Błąd: {e}", exc_info=True, extra={"json_fields": {"pnl_record": pnl_record}})
            continue
            
    # Krok 3: Zaktualizuj znacznik czasu na sam koniec, po przetworzeniu wszystkich znalezionych rekordów.
    save_last_processed_timestamp(current_cycle_timestamp, "pnl_logger_last_fetch_state")
    logger.info(f"[PNL_LOGGER] Zakończono cykl. Przetworzono {processed_count} rekordów. Zaktualizowano znacznik czasu na {current_cycle_timestamp.isoformat()}.")
    ### KONIEC POPRAWKI ###
    
    return processed_count

def _run_analysis_of_existing_cases():
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return

    # Krok 1: Pobieramy zasady instrumentów, aby mieć dostęp do 'tickSize'
    instrument_rules = get_instrument_rules()
    if not instrument_rules:
        logger.error("Nie udało się wczytać zasad instrumentów dla procesu analitycznego. Pomijam cykl.")
        return

    logger.info(f"[DIAGNOSTYKA] Znaleziono {len(all_cases_docs)} teczek analitycznych do przetworzenia.")
    symbols_to_watch = {doc.to_dict().get('symbol') for doc in all_cases_docs if doc.to_dict()}
    valid_symbols = {s for s in symbols_to_watch if s}
    
    if not valid_symbols:
        logger.info("Brak symboli do monitorowania w aktywnych teczkach.")
        return

    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych kline z cache'u. Pomijam cykl.")
        return

    klines_models = {
        symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()
    }
    logger.info(f"[DIAGNOSTYKA] Pomyślnie pobrano {len(klines_models)} świec z cache'u.")

    for case_doc_snapshot in all_cases_docs:
        case_id = case_doc_snapshot.id
        try:
            case_doc = case_doc_snapshot.to_dict()
            symbol = case_doc.get('symbol')
            status = case_doc.get('status')
            
            # Krok 2: Pobieramy zasady dla konkretnego symbolu
            rule = instrument_rules.get(symbol)
            if not rule or "tickSize" not in rule:
                logger.warning(f"Brak zasad 'tickSize' dla symbolu {symbol} (teczka {case_id}). Pomijam.")
                continue

            latest_kline = klines_models.get(symbol)
            if not latest_kline:
                logger.warning(f"Brak danych kline dla symbolu {symbol} (teczka {case_id}). Pomijam tę teczkę w cyklu.")
                continue
            
            # Krok 3: Przekazujemy 'rule' do funkcji obsługujących
            if status == 'PENDING':
                _handle_pending_case(case_doc_snapshot, latest_kline, rule)
            elif status == 'TRIGGERED':
                _handle_triggered_case(case_doc_snapshot, latest_kline, rule)
        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania teczki {case_id}: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę cyklu analitycznego.")

def _handle_pending_case(case_doc_snapshot: Any, kline: Kline, rule: Dict[str, Any]):
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    tick_size = rule['tickSize']

    # Używamy tych samych, zaokrąglonych cen, co w procesie transakcyjnym
    if alert.direction == 'LONG':
        final_entry = round_price_by_tick(alert.entry, tick_size, 'up')
    else: # SHORT
        final_entry = round_price_by_tick(alert.entry, tick_size, 'down')
    
    entry_triggered = False
    if alert.direction == 'LONG' and kline.low <= final_entry:
        entry_triggered = True
    elif alert.direction == 'SHORT' and kline.high >= final_entry:
        entry_triggered = True
        
    if entry_triggered:
        logger.info(f"--- [ANALYSIS TRIGGER] --- [{alert.symbol}] | ID: {case_id} | Cena wejścia {final_entry} dotknięta.")
        updates = {
            "status": "TRIGGERED",
            "triggered_at": datetime.now(timezone.utc)
        }
        state_manager.update_case_status_and_results(case_id, updates)



def _handle_triggered_case(case_doc_snapshot: Any, kline: Kline, rule: Dict[str, Any]):
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    symbol = case_doc.get('symbol')
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    results = case_doc.get('results', {})
    tick_size = rule['tickSize']

    unresolved_targets = {k: v for k, v in results.items() if v == "UNRESOLVED"}
    if not unresolved_targets:
        logger.info(f"[{case_id}] Wszystkie scenariusze rozstrzygnięte. Usuwam teczkę.")
        state_manager.delete_case_by_id(case_id)
        return

    direction = alert.direction
    
    # Używamy tych samych, zaokrąglonych cen, co w procesie transakcyjnym
    if direction == 'LONG':
        final_sl = round_price_by_tick(alert.sl, tick_size, 'down')
    else: # SHORT
        final_sl = round_price_by_tick(alert.sl, tick_size, 'up')
    
    resolved_scenarios = {}
    close_timestamp = datetime.fromtimestamp(kline.timestamp / 1000, tz=timezone.utc)
    base_log_data = {
        "analysis_id": case_id, "symbol": symbol, "direction": direction,
        "entry_price": alert.entry, "sl_price": alert.sl, # W logach BQ zapisujemy surowe ceny
        "timestamp_alert": alert.received_at.isoformat() if alert.received_at else None,
        "timestamp_entry": case_doc.get('triggered_at').isoformat() if case_doc.get('triggered_at') else None,
        "timestamp_close": close_timestamp.isoformat(),
        "risk_percentage": _calculate_risk_percentage(alert.entry, alert.sl)
    }

    # "Zasada Pesymisty": Najpierw sprawdzamy SL
    sl_hit = (direction == 'LONG' and kline.low <= final_sl) or (direction == 'SHORT' and kline.high >= final_sl)

    if sl_hit:
        logger.info(f"--- [ANALYSIS SL HIT] --- [{symbol}] | ID: {case_id} | Wszystkie nierozstrzygnięte scenariusze = LOSE.")
        for target_level in unresolved_targets:
            log_data = base_log_data.copy()
            log_data.update({"target_level": target_level, "target_price": getattr(alert, target_level), "result": "LOSE"})
            log_analysis_result(log_data)
            resolved_scenarios[f'results.{target_level}'] = "LOSE"
    else:
        # Dopiero jeśli SL nie został trafiony, sprawdzamy TP
        for target_level in unresolved_targets:
            # Używamy zaokrąglonych cen TP
            if direction == 'LONG':
                final_tp = round_price_by_tick(getattr(alert, target_level), tick_size, 'up')
            else: # SHORT
                final_tp = round_price_by_tick(getattr(alert, target_level), tick_size, 'down')

            tp_hit = (direction == 'LONG' and kline.high >= final_tp) or (direction == 'SHORT' and kline.low <= final_tp)
            
            if tp_hit:
                logger.info(f"--- [ANALYSIS TP HIT] --- [{symbol}] | ID: {case_id} | Scenariusz {target_level} = WIN.")
                log_data = base_log_data.copy()
                log_data.update({"target_level": target_level, "target_price": getattr(alert, target_level), "result": "WIN"})
                log_analysis_result(log_data)
                resolved_scenarios[f'results.{target_level}'] = "WIN"

    if resolved_scenarios:
        state_manager.update_case_status_and_results(case_id, resolved_scenarios)
        if len(results) - len(unresolved_targets) + len(resolved_scenarios) >= 6:
            logger.info(f"[{case_id}] Wszystkie 6 scenariuszy rozstrzygnięte. Finalne usunięcie teczki.")
            state_manager.delete_case_by_id(case_id)