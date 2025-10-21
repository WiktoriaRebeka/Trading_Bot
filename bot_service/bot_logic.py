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
# Ten import musi wskazywać na plik pnl_logger_real.py
from bot_service.pnl_logger_real import log_real_trade_result
from bot_service.bybit_executor import BybitExecutor, BybitAPIError
from bot_service.fetch_from_firestore import fetch_new_alerts_since, save_last_processed_timestamp, load_last_processed_timestamp

logger = logging.getLogger(__name__)


def run_combined_cycle(executor: BybitExecutor):
    """Główna, połączona pętla logiki z zapewnioną atomowością."""
    logger.info("Uruchamiam połączony cykl analityczno-transakcyjny.")
    
    try:
        log_closed_positions_pnl(executor)
    except Exception as e:
        logger.error(f"Błąd podczas logowania PnL w cyklu połączonym: {e}", exc_info=True)

    last_ts = load_last_processed_timestamp("main_cycle_last_fetch_state")
    new_alerts, new_ts = fetch_new_alerts_since(last_ts)

    if new_alerts:
        process_alerts_atomically(new_alerts, executor)
        
        if new_ts and (not last_ts or new_ts > last_ts):
            save_last_processed_timestamp(new_ts, "main_cycle_last_fetch_state")
    
    _run_analysis_of_existing_cases()
    logger.info("Zakończono połączony cykl analityczno-transakcyjny.")

# W pliku bot_service/bot_logic.py

# Zastąp CAŁĄ funkcję process_alerts_atomically poniższą wersją:

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

            open_position_side = executor.get_open_position_side(symbol)
            
            if open_position_side and open_position_side != "ERROR":
                logger.info(f"[{symbol}] Wykryto otwartą pozycję: {open_position_side}.")
                if alert_model.direction != open_position_side:
                    logger.warning(f"[{symbol}] ODRZUCONO (Strażnik Pozycji): Nowy alert ({alert_model.direction}) jest przeciwny do otwartej pozycji.")
                    processed_symbols_in_cycle.add(symbol)
                    continue
                else:
                    logger.info(f"[{symbol}] Nowy alert jest zgodny z otwartą pozycją. Kontynuuję przetwarzanie bez anulowania zleceń.")
            else:
                logger.info(f"[{symbol}] Brak otwartej pozycji. Anuluję wszystkie oczekujące zlecenia limit dla tego symbolu.")
                executor.cancel_all_open_orders_for_symbol(symbol)

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
                final_tp = round_price_by_tick(alert_model.tp_3_0, tick_size, 'up')
            else: # SHORT
                final_entry = round_price_by_tick(alert_model.entry, tick_size, 'down')
                final_sl = round_price_by_tick(alert_model.sl, tick_size, 'up')
                final_tp = round_price_by_tick(alert_model.tp_3_0, tick_size, 'down')

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
            
            custom_order_link_id = f"bot_{alert_id.replace('-', '')[:16]}_{int(datetime.now().timestamp())}"

            order_params = {
                "symbol": symbol, "side": "Buy" if alert_model.direction == "LONG" else "Sell",
                "orderType": "Limit", "qty": final_qty, "price": final_entry,
                "takeProfit": final_tp,
                "stopLoss": final_sl,
                "tpTriggerBy": "MarkPrice",
                "slTriggerBy": "MarkPrice",
                "orderLinkId": custom_order_link_id,
                "timeInForce": "GTC"
            }

            response = executor.place_order(order_params)
            
            if response and response.get('orderId'):
                order_id = response.get('orderId')
                logger.info(f"[{symbol}] Zlecenie zintegrowane pomyślnie złożone. Order ID: {order_id}")
                order_data_to_save = {
                    "symbol": symbol,
                    "orderId": order_id,
                    "orderLinkId": custom_order_link_id,
                    "status": "NEW_BRACKET",
                    "alert_id": alert_id,
                    "direction": alert_model.direction,
                    "final_entry_price": final_entry,
                    "final_sl_price": final_sl, 
                    "final_tp_price": final_tp,
                    "tp_price_chart": alert_model.tp_3_0
                }
                state_manager.save_active_order(order_id, order_data_to_save)
            else:
                raise Exception("Nie udało się złożyć zlecenia zintegrowanego (brak odpowiedzi od Bybit).")

            processed_symbols_in_cycle.add(symbol)

        # --- POCZĄTEK POPRAWKI: Dodanie brakującego bloku 'except' ---
        except BybitAPIError as e:
            if e.ret_code == 110093:
                logger.warning(f"[{symbol}] Zlecenie odrzucone (110093) z powodu ustawień margin. Pomijam.")
            else:
                logger.error(f"Błąd API Bybit dla alertu {alert_id}: {e}", exc_info=False)
            processed_symbols_in_cycle.add(symbol)
        except Exception as e:
            logger.error(f"Krytyczny błąd podczas atomowego przetwarzania alertu {alert_id}: {e}", exc_info=True)
            processed_symbols_in_cycle.add(symbol)
        # --- KONIEC POPRAWKI ---

def _transform_liquidation_record(liq_record: Dict[str, Any]) -> Dict[str, Any]:
    """Tłumaczy rekord likwidacji na format zgodny z rekordem PnL."""
    return {
        "symbol": liq_record.get("symbol"),
        "orderId": f"liq_{liq_record.get('symbol')}_{liq_record.get('updatedTime')}",
        "side": "Buy" if liq_record.get("side") == "Sell" else "Sell",
        "qty": liq_record.get("size"),
        "avgEntryPrice": None,
        "avgExitPrice": liq_record.get("deliveryPrice"),
        "closedPnl": liq_record.get("realisedPnl"),
        "cumCommission": "0",
        "leverage": None,
        "createdTime": liq_record.get("updatedTime"),
        "updatedTime": liq_record.get("updatedTime"),
        "exitType": "Liquidation"
    }


def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania PnL (w tym likwidacji).")
    
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    current_cycle_start_time = datetime.now(timezone.utc)
    
    LOOKBACK_BUFFER_HOURS = 24
    start_time_with_buffer = last_check_ts_dt - timedelta(hours=LOOKBACK_BUFFER_HOURS)
    logger.info(f"[PNL_LOGGER] Sprawdzam zamknięte pozycje od: {start_time_with_buffer.isoformat()} (z {LOOKBACK_BUFFER_HOURS}h buforem).")
    start_time_ms = int(start_time_with_buffer.timestamp() * 1000)
    
    all_records = []
    try:
        pnl_records = executor.get_closed_pnl_history(start_time_ms=start_time_ms)
        all_records.extend(pnl_records)
        liq_records_raw = executor.get_liquidation_history(start_time_ms=start_time_ms)
        all_records.extend([_transform_liquidation_record(rec) for rec in liq_records_raw])
    except Exception as e:
        logger.critical(f"[PNL_LOGGER] Krytyczny błąd podczas pobierania historii z Bybit: {e}", exc_info=True)
        return 0

    if not all_records:
        logger.info("[PNL_LOGGER] Nie znaleziono żadnych zamkniętych pozycji od ostatniego sprawdzenia.")
        save_last_processed_timestamp(current_cycle_start_time, "pnl_logger_last_fetch_state")
        return 0

    logger.info(f"[PNL_LOGGER] Znaleziono łącznie {len(all_records)} zamkniętych pozycji. Rozpoczynam przetwarzanie.")
    pnl_records_sorted = sorted(all_records, key=lambda r: int(r.get("updatedTime", 0)))
    
    processed_count = 0
    new_max_ts_dt = last_check_ts_dt

    for pnl_record in pnl_records_sorted:
        order_id = pnl_record.get("orderId")
        symbol = pnl_record.get("symbol")
        
        try:
            # --- POCZĄTEK POPRAWIONEJ LOGIKI ---
            if not order_id or not symbol:
                logger.warning("[PNL_LOGGER] Pominięto rekord bez orderId lub symbolu.", extra={"json_fields": {"pnl_record": pnl_record}})
                continue

            logger.info(f"[PNL_LOGGER] Przetwarzanie rekordu dla {symbol} [OrderID: {order_id}]")
            
            active_order_data = state_manager.get_active_order_by_id(order_id)
            
            if not active_order_data:
                logger.warning(f"[PNL_LOGGER] Nie znaleziono dopasowania dla orderId '{order_id}'. Transakcja zostanie zapisana jako UNMATCHED.")
                active_order_data = {} # Użyj pustego słownika, aby zapisać transakcję

            # Zawsze wywołujemy zapis do BigQuery
            if log_real_trade_result(pnl_record, active_order_data):
                processed_count += 1
            
            # Sprzątamy, tylko jeśli było dopasowanie
            if active_order_data:
                logger.info(f"[PNL_LOGGER] Sprzątanie: Usuwanie dokumentu '{order_id}' z kolekcji active_orders.")
                state_manager.delete_active_order_by_id(order_id)
            
            # --- KONIEC POPRAWIONEJ LOGIKI ---

            # NIEZALEŻNIE OD WYNIKU, aktualizujemy nasz postęp w pętli
            updated_time_ms = int(pnl_record.get("updatedTime", 0))
            if updated_time_ms > 0:
                record_ts_dt = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
                if record_ts_dt > new_max_ts_dt:
                    new_max_ts_dt = record_ts_dt

        except Exception as e:
            logger.error(f"[PNL_LOGGER] Krytyczny błąd podczas przetwarzania rekordu dla symbolu {symbol}. Błąd: {e}", exc_info=True)
            continue
    
    # Po zakończeniu pętli, zapisujemy NAJNOWSZY timestamp, jaki widzieliśmy
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

def _run_analysis_of_existing_cases():
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return
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
    klines_models = {symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()}
    logger.info(f"[DIAGNOSTYKA] Pomyślnie pobrano {len(klines_models)} świec z cache'u.")
    for case_doc_snapshot in all_cases_docs:
        case_id = case_doc_snapshot.id
        try:
            case_doc = case_doc_snapshot.to_dict()
            symbol = case_doc.get('symbol')
            status = case_doc.get('status')
            rule = instrument_rules.get(symbol)
            if not rule or "tickSize" not in rule:
                logger.warning(f"Brak zasad 'tickSize' dla symbolu {symbol} (teczka {case_id}). Pomijam.")
                continue
            latest_kline = klines_models.get(symbol)
            if not latest_kline:
                logger.warning(f"Brak danych kline dla symbolu {symbol} (teczka {case_id}). Pomijam tę teczkę w cyklu.")
                continue
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
        updates = {"status": "TRIGGERED", "triggered_at": datetime.now(timezone.utc)}
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
    if direction == 'LONG':
        final_sl = round_price_by_tick(alert.sl, tick_size, 'down')
    else: # SHORT
        final_sl = round_price_by_tick(alert.sl, tick_size, 'up')
    resolved_scenarios = {}
    close_timestamp = datetime.fromtimestamp(kline.timestamp / 1000, tz=timezone.utc)
    base_log_data = {
        "analysis_id": case_id, "symbol": symbol, "direction": direction,
        "entry_price": alert.entry, "sl_price": alert.sl,
        "timestamp_alert": alert.received_at.isoformat() if alert.received_at else None,
        "timestamp_entry": case_doc.get('triggered_at').isoformat() if case_doc.get('triggered_at') else None,
        "timestamp_close": close_timestamp.isoformat(),
        "risk_percentage": _calculate_risk_percentage(alert.entry, alert.sl)
    }
    sl_hit = (direction == 'LONG' and kline.low <= final_sl) or (direction == 'SHORT' and kline.high >= final_sl)
    if sl_hit:
        logger.info(f"--- [ANALYSIS SL HIT] --- [{symbol}] | ID: {case_id} | Wszystkie nierozstrzygnięte scenariusze = LOSE.")
        for target_level in unresolved_targets:
            log_data = base_log_data.copy()
            log_data.update({"target_level": target_level, "target_price": getattr(alert, target_level), "result": "LOSE"})
            log_analysis_result(log_data)
            resolved_scenarios[f'results.{target_level}'] = "LOSE"
    else:
        for target_level in unresolved_targets:
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