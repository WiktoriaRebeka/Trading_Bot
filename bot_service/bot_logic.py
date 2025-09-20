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
    """Główna, połączona pętla logiki."""
    logger.info("Uruchamiam połączony cykl analityczno-transakcyjny.")
    
    last_ts = load_last_processed_timestamp("main_cycle_last_fetch_state")
    new_alerts, new_ts = fetch_new_alerts_since(last_ts)

    if new_alerts:
        process_alerts_transactional(new_alerts, executor)
        process_new_alerts_analytical(new_alerts)
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

def _is_alert_still_valid(alert: AlertData, current_price: float) -> bool:
    """
    Sprawdza, czy alert nie jest przestarzały w kontekście aktualnej ceny rynkowej.
    Zwraca False, jeśli poziom SL został już naruszony.
    """
    if alert.direction == 'LONG':
        if current_price <= alert.sl:
            logger.warning(
                f"[{alert.symbol}] ODRZUCONO PRZESTARZAŁY ALERT (LONG). "
                f"Aktualna cena ({current_price}) jest już poniżej lub równa SL ({alert.sl})."
            )
            return False
    elif alert.direction == 'SHORT':
        if current_price >= alert.sl:
            logger.warning(
                f"[{alert.symbol}] ODRZUCONO PRZESTARZAŁY ALERT (SHORT). "
                f"Aktualna cena ({current_price}) jest już powyżej lub równa SL ({alert.sl})."
            )
            return False
            
    logger.info(f"[{alert.symbol}] Alert jest aktualny. Aktualna cena: {current_price}, SL: {alert.sl}, Kierunek: {alert.direction}.")
    return True

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
    if risk_perc is None or risk_perc < 0.05:
        logger.warning(
            f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej minimum 0.05%. "
            f"Obliczone ryzyko: {risk_perc}% (Wejście: {alert.entry}, SL: {alert.sl})."
        )
        return False
    
    logger.info(
        f"Alert [{alert.symbol}] przeszedł walidację. "
        f"Kierunek: {alert.direction}, Ryzyko: {risk_perc}%."
    )
    return True

def process_alerts_transactional(alerts: List[Dict[str, Any]], executor: BybitExecutor):
    """
    Przetwarza alerty, składając trzy oddzielne zlecenia.
    """
    if not alerts:
        return

    for alert_dict in alerts:
        alert_id = alert_dict.get('id', 'unknown')
        alert = None

        try:
            alert = AlertData.model_validate(alert_dict)
            symbol = alert.symbol
            
            logger.info(f"[{symbol}] Otrzymano nowy alert. Anuluję wszystkie oczekujące zlecenia LIMIT, aby przygotować miejsce.")
            executor.cancel_all_open_orders_for_symbol(symbol)
            
            if not _correct_and_validate_alert(alert):
                continue

            current_price = executor.get_latest_ticker_price(symbol)
            if current_price is None:
                logger.error(f"[{symbol}] Nie udało się pobrać aktualnej ceny rynkowej. Pomijam alert {alert_id}.")
                continue
            
            if not _is_alert_still_valid(alert, current_price):
                continue
            
            rule = executor.get_instrument_info(symbol)
            if not rule or not rule.get("tickSize") or not rule.get("qtyStep"):
                raise Exception("Nie udało się pobrać zasad instrumentu.")
            
            tick_size, qty_step = rule["tickSize"], rule["qtyStep"]

            if alert.direction == 'LONG':
                alert.entry = round_price_by_tick(alert.entry, tick_size, 'up')
                alert.sl = round_price_by_tick(alert.sl, tick_size, 'down')
                alert.tp_3_0 = round_price_by_tick(alert.tp_3_0, tick_size, 'up')
            elif alert.direction == 'SHORT':
                alert.entry = round_price_by_tick(alert.entry, tick_size, 'down')
                alert.sl = round_price_by_tick(alert.sl, tick_size, 'up')
                alert.tp_3_0 = round_price_by_tick(alert.tp_3_0, tick_size, 'down')

            risk_usdt = float(os.getenv("RISK_PER_TRADE_USDT", "2.5"))
            final_qty = calculate_position_size(
                risk_per_trade_usdt=risk_usdt, entry_price=alert.entry,
                sl_price=alert.sl, qty_step=qty_step
            )

            if not final_qty or final_qty <= 0:
                continue
            
            entry_order_id = f"entry_{alert_id.replace('-', '')[:12]}_{int(datetime.now().timestamp())}"
            entry_params = {
                "symbol": symbol,
                "side": "Buy" if alert.direction == "LONG" else "Sell",
                "orderType": "Limit",
                "qty": final_qty,
                "price": alert.entry,
                "orderLinkId": entry_order_id,
                "reduceOnly": False
            }
            entry_response = executor.place_order(entry_params)
            if not entry_response:
                raise Exception("Krok 1/3: Nie udało się złożyć zlecenia wejściowego.")
            
            logger.info(f"[{symbol}] Krok 1/3: Zlecenie wejściowe (LIMIT) pomyślnie złożone.")

            sl_params = {
                "symbol": symbol,
                "side": "Sell" if alert.direction == "LONG" else "Buy",
                "orderType": "Market",
                "qty": final_qty,
                "triggerPrice": alert.sl,
                "triggerDirection": "Falling" if alert.direction == "LONG" else "Rising",
                "reduceOnly": True
            }
            sl_response = executor.place_order(sl_params)
            if not sl_response:
                raise Exception("Krok 2/3: Nie udało się złożyć zlecenia Stop Loss.")

            logger.info(f"[{symbol}] Krok 2/3: Zlecenie Stop Loss (MARKET) pomyślnie złożone.")

            tp_params = {
                "symbol": symbol,
                "side": "Sell" if alert.direction == "LONG" else "Buy",
                "orderType": "Limit",
                "qty": final_qty,
                "price": alert.tp_3_0,
                "triggerPrice": alert.tp_3_0,
                "triggerDirection": "Rising" if alert.direction == "LONG" else "Falling",
                "reduceOnly": True
            }
            tp_response = executor.place_order(tp_params)
            if not tp_response:
                raise Exception("Krok 3/3: Nie udało się złożyć zlecenia Take Profit.")

            logger.info(f"[{symbol}] Krok 3/3: Zlecenie Take Profit (LIMIT) pomyślnie złożone. Pełen zestaw zleceń jest aktywny.")

            state_manager.save_active_order(
                entry_response.get("orderId"),
                {"symbol": symbol, "orderId": entry_response.get("orderId"), "status": "NEW_BRACKET", "alert_id": alert_id}
            )

        # === NOWY, POPRAWIONY BLOK OBSŁUGI BŁĘDÓW ===
        except BybitAPIError as e:
            if e.ret_code == 110093:
                logger.warning(
                    f"[{alert.symbol if alert else 'N/A'}] Zlecenie odrzucone przez Bybit (110093) z powodu opóźnienia/race condition. "
                    f"Alert stał się przestarzały między weryfikacją a złożeniem zlecenia. Pomijam."
                )
                # Celowo nie robimy tu awaryjnego anulowania, ponieważ błąd wystąpił
                # podczas składania zlecenia SL/TP, a zlecenie wejściowe mogło już zostać złożone.
                # Pozostawienie go do anulowania w następnym cyklu jest bezpieczniejsze.
            else:
                # Inne błędy API traktujemy jak dotychczas
                logger.error(f"Błąd API Bybit w procesie składania zleceń dla alertu {alert_id}: {e}", exc_info=False)
                logger.warning(f"[{alert.symbol if alert else 'N/A'}] ANULOWANIE AWARYJNE: Próba anulowania wszystkich zleceń z powodu błędu.")
                if alert and alert.symbol:
                    executor.cancel_all_open_orders_for_symbol(alert.symbol)
        except Exception as e:
            logger.error(f"Błąd w procesie składania zleceń dla alertu {alert_id}: {e}", exc_info=False)
            logger.warning(f"[{alert.symbol if alert else 'N/A'}] ANULOWANIE AWARYJNE: Próba anulowania wszystkich zleceń z powodu błędu.")
            if alert and alert.symbol:
                executor.cancel_all_open_orders_for_symbol(alert.symbol)

# ... reszta pliku (log_closed_positions_pnl, process_new_alerts_analytical, etc.) pozostaje bez zmian ...
# Poniżej wklejam resztę pliku dla kompletności.

def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    """
    Pobiera historię zamkniętych pozycji, dopasowuje je po symbolu z Firestore i loguje do BigQuery.
    """
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania PnL.")
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    logger.info(f"[PNL_LOGGER] Sprawdzam zamknięte pozycje od: {last_check_ts_dt.isoformat()}")
    
    start_time_ms = int(last_check_ts_dt.timestamp() * 1000)
    pnl_records = executor.get_closed_pnl_history(start_time_ms=start_time_ms)
    
    if not pnl_records:
        logger.info("[PNL_LOGGER] Nie znaleziono nowych zamkniętych pozycji na Bybit od ostatniego sprawdzenia.")
        return 0

    logger.info(f"[PNL_LOGGER] Znaleziono {len(pnl_records)} zamkniętych pozycji na Bybit. Rozpoczynam przetwarzanie.")
    new_max_ts = last_check_ts_dt
    processed_count = 0
    
    for pnl_record in pnl_records:
        symbol = pnl_record.get("symbol")
        if not symbol:
            logger.warning("[PNL_LOGGER] Pominięto rekord PnL bez symbolu.", extra={"json_fields": {"pnl_record": pnl_record}})
            continue

        active_order_data = state_manager.get_active_order_by_symbol(symbol)
        
        if not active_order_data:
            logger.warning(f"[PNL_LOGGER] Nie znaleziono aktywnego zlecenia dla symbolu {symbol} w Firestore. Prawdopodobnie transakcja manualna. Pomijam.")
            continue

        alert_id = active_order_data.get('alert_id', 'unknown')
        original_order_id = active_order_data.get('orderId')

        if not original_order_id:
            logger.error(f"[PNL_LOGGER] Krytyczny błąd: znaleziono dopasowanie dla {symbol}, ale brak orderId w dokumencie Firestore. Pomijam.", extra={"json_fields": active_order_data})
            continue

        logger.info(f"[PNL_LOGGER] Pomyślnie dopasowano zamkniętą pozycję {symbol} do alertu {alert_id} (Order ID: {original_order_id}).")

        enriched_pnl_data = pnl_record.copy()
        enriched_pnl_data['alert_id'] = alert_id
        
        log_real_trade_result(enriched_pnl_data)
        processed_count += 1
        
        state_manager.delete_active_order_by_id(original_order_id)

        updated_time_ms = int(pnl_record.get("updatedTime", 0))
        if updated_time_ms > 0:
            record_ts = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
            if record_ts > new_max_ts:
                new_max_ts = record_ts
    
    if new_max_ts > last_check_ts_dt:
        logger.info(f"[PNL_LOGGER] Zapisuję nowy timestamp ostatniego sprawdzenia: {new_max_ts.isoformat()}")
        save_last_processed_timestamp(new_max_ts + timedelta(seconds=1), "pnl_logger_last_fetch_state")
        
    logger.info(f"[PNL_LOGGER] Zakończono cykl. Przetworzono i zalogowano {processed_count} rekordów.")
    return processed_count


def process_new_alerts_analytical(newly_fetched_alerts: List[Dict[str, Any]]):
    """Przetwarza nowe alerty, tworząc teczki analityczne (tryb analityczny)."""
    if not newly_fetched_alerts:
        return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów (tryb analityczny).")

    instrument_rules = get_instrument_rules()
    if not instrument_rules:
        logger.error("Nie udało się wczytać zasad instrumentów z Firestore. Przerywam przetwarzanie alertów.")
        return
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'unknown')
        if alert_id == 'unknown':
            logger.error("Otrzymano alert bez ID. Pomijam.", extra={"json_fields": {"alert_data": alert_dict}})
            continue

        try:
            alert_data_model = AlertData.model_validate(alert_dict)
            
            symbol = alert_data_model.symbol
            rule = instrument_rules.get(symbol)

            if not rule or "tickSize" not in rule:
                logger.warning(f"Brak reguły 'tickSize' dla symbolu {symbol}. Pomijam zaokrąglanie.")
            else:
                tick_size = rule["tickSize"]
                
                if alert_data_model.direction == 'LONG':
                    alert_data_model.entry = round_price_by_tick(alert_data_model.entry, tick_size, 'up')
                    alert_data_model.sl = round_price_by_tick(alert_data_model.sl, tick_size, 'down')
                    alert_data_model.tp_1_0 = round_price_by_tick(alert_data_model.tp_1_0, tick_size, 'up')
                    alert_data_model.tp_1_5 = round_price_by_tick(alert_data_model.tp_1_5, tick_size, 'up')
                    alert_data_model.tp_2_0 = round_price_by_tick(alert_data_model.tp_2_0, tick_size, 'up')
                    alert_data_model.tp_3_0 = round_price_by_tick(alert_data_model.tp_3_0, tick_size, 'up')
                    alert_data_model.tp_4_0 = round_price_by_tick(alert_data_model.tp_4_0, tick_size, 'up')
                    alert_data_model.tp_5_0 = round_price_by_tick(alert_data_model.tp_5_0, tick_size, 'up')
                elif alert_data_model.direction == 'SHORT':
                    alert_data_model.entry = round_price_by_tick(alert_data_model.entry, tick_size, 'down')
                    alert_data_model.sl = round_price_by_tick(alert_data_model.sl, tick_size, 'up')
                    alert_data_model.tp_1_0 = round_price_by_tick(alert_data_model.tp_1_0, tick_size, 'down')
                    alert_data_model.tp_1_5 = round_price_by_tick(alert_data_model.tp_1_5, tick_size, 'down')
                    alert_data_model.tp_2_0 = round_price_by_tick(alert_data_model.tp_2_0, tick_size, 'down')
                    alert_data_model.tp_3_0 = round_price_by_tick(alert_data_model.tp_3_0, tick_size, 'down')
                    alert_data_model.tp_4_0 = round_price_by_tick(alert_data_model.tp_4_0, tick_size, 'down')
                    alert_data_model.tp_5_0 = round_price_by_tick(alert_data_model.tp_5_0, tick_size, 'down')

            existing_pending_case = state_manager.get_pending_case_for_symbol(alert_data_model.symbol)
            if existing_pending_case:
                logger.info(f"[{alert_data_model.symbol}] Nowy alert ({alert_id}) unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). Usuwam.")
                state_manager.delete_case_by_id(existing_pending_case.id)

            is_valid = _correct_and_validate_alert(alert_data_model)
            
            if is_valid:
                logger.info(f"[{alert_data_model.symbol}] Alert ({alert_id}) przeszedł walidację. Tworzę teczkę PENDING.")
                new_case = AnalyticalCase(
                    alert_id=alert_data_model.id,
                    symbol=alert_data_model.symbol,
                    alert_data=alert_data_model.model_dump(by_alias=True)
                )
                state_manager.create_analytical_case(new_case)
            else:
                logger.warning(f"[{alert_data_model.symbol}] Nowy alert ({alert_id}) został odrzucony po walidacji. Nie tworzę nowej teczki PENDING.")

        except ValidationError as e:
            logger.error(f"Błąd walidacji Pydantic dla alertu ({alert_id}): {e}", extra={"json_fields": {"alert_id": alert_id, "alert_data": alert_dict}})
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ({alert_id}): {e}", exc_info=True, extra={"json_fields": {"alert_id": alert_id}})


def _run_analysis_of_existing_cases():
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
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
            
            latest_kline = klines_models.get(symbol)
            if not latest_kline:
                logger.warning(f"Brak danych kline dla symbolu {symbol} (teczka {case_id}). Pomijam tę teczkę w cyklu.")
                continue
            
            if status == 'PENDING':
                _handle_pending_case(case_doc_snapshot, latest_kline)
            elif status == 'TRIGGERED':
                _handle_triggered_case(case_doc_snapshot, latest_kline)
        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania teczki {case_id}: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę cyklu analitycznego.")

def _handle_pending_case(case_doc_snapshot: Any, kline: Kline):
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    entry_price = alert.entry
    
    logger.info(
        f"[DIAGNOSTYKA PENDING][{case_id}] Sprawdzam warunek wejścia dla {alert.symbol} ({alert.direction}). "
        f"Entry: {entry_price}, Kline Low: {kline.low}, Kline High: {kline.high}"
    )
    
    entry_triggered = False
    if alert.direction == 'LONG' and kline.low <= entry_price:
        entry_triggered = True
    elif alert.direction == 'SHORT' and kline.high >= entry_price:
        entry_triggered = True
        
    if entry_triggered:
        logger.info(f"--- [TRIGGER] --- [{alert.symbol}] | ID: {case_id} | Cena wejścia {entry_price} dotknięta.")
        updates = {
            "status": "TRIGGERED",
            "triggered_at": datetime.now(timezone.utc)
        }
        state_manager.update_case_status_and_results(case_id, updates)



def _handle_triggered_case(case_doc_snapshot: Any, kline: Kline):
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    symbol = case_doc.get('symbol')
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    results = case_doc.get('results', {})

    unresolved_targets = {k: v for k, v in results.items() if v == "UNRESOLVED"}
    if not unresolved_targets:
        logger.info(f"[{case_id}] Wszystkie scenariusze rozstrzygnięte. Usuwam teczkę.")
        state_manager.delete_case_by_id(case_id)
        return

    sl_price = alert.sl
    direction = alert.direction
    sl_hit = (direction == 'LONG' and kline.low <= sl_price) or (direction == 'SHORT' and kline.high >= sl_price)
    
    resolved_scenarios = {}
    close_timestamp = datetime.fromtimestamp(kline.timestamp / 1000, tz=timezone.utc)
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    triggered_at_dt = case_doc.get('triggered_at')
    received_at_dt = alert.received_at

    base_log_data = {
        "analysis_id": case_id,
        "symbol": symbol,
        "direction": direction,
        "entry_price": alert.entry,
        "sl_price": sl_price,
        "timestamp_alert": received_at_dt.isoformat() if received_at_dt else None,
        "timestamp_entry": triggered_at_dt.isoformat() if triggered_at_dt else None,
        "timestamp_close": close_timestamp.isoformat(),
        "risk_percentage": risk_perc
    }

    if sl_hit:
        logger.info(f"--- [SL HIT] --- [{symbol}] | ID: {case_id} | Wszystkie nierozstrzygnięte scenariusze = LOSE.")
        for target_level in unresolved_targets:
            log_data = base_log_data.copy()
            log_data.update({"target_level": target_level, "target_price": getattr(alert, target_level), "result": "LOSE"})
            log_analysis_result(log_data)
            resolved_scenarios[f'results.{target_level}'] = "LOSE"
    else:
        for target_level in unresolved_targets:
            target_price = getattr(alert, target_level)
            tp_hit = (direction == 'LONG' and kline.high >= target_price) or (direction == 'SHORT' and kline.low <= target_price)
            if tp_hit:
                logger.info(f"--- [TP HIT] --- [{symbol}] | ID: {case_id} | Scenariusz {target_level} = WIN.")
                log_data = base_log_data.copy()
                log_data.update({"target_level": target_level, "target_price": target_price, "result": "WIN"})
                log_analysis_result(log_data)
                resolved_scenarios[f'results.{target_level}'] = "WIN"

    if resolved_scenarios:
        state_manager.update_case_status_and_results(case_id, resolved_scenarios)
        if len(results) - len(unresolved_targets) + len(resolved_scenarios) >= 6:
            logger.info(f"[{case_id}] Wszystkie 6 scenariuszy rozstrzygnięte. Finalne usunięcie teczki.")
            state_manager.delete_case_by_id(case_id)