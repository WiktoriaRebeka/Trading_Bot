# Lokalizacja: bot_service/bot_logic.py


import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import ValidationError

from shared_lib.models import AlertData, Kline, AnalyticalCase
from bot_service import state_manager
from bot_service.bigquery_logger import log_analysis_result
from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

logger = logging.getLogger(__name__)

def _calculate_risk_percentage(entry_price: float, sl_price: float) -> Optional[float]:
    if entry_price == 0: return None
    risk_distance = abs(entry_price - sl_price)
    return round((risk_distance / entry_price) * 100, 4)

def _validate_alert_logic(alert: AlertData) -> bool:
    is_long_ok = (alert.direction == 'LONG' and alert.sl < alert.entry)
    is_short_ok = (alert.direction == 'SHORT' and alert.sl > alert.entry)
    if not (is_long_ok or is_short_ok):
        logger.warning(f"[{alert.symbol}] Odrzucono alert: Nielogiczna pozycja (Kierunek: {alert.direction}, Wejście: {alert.entry}, SL: {alert.sl}).")
        return False

    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None or risk_perc < 0.05:
        logger.warning(f"[{alert.symbol}] Odrzucono alert: Ryzyko poniżej minimum 0.05% (wynosi {risk_perc}%).")
        return False
    
    return True

def _process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'unknown')
        try:
            alert_data = AlertData.model_validate(alert_dict)
            
            existing_pending_case = state_manager.get_pending_case_for_symbol(alert_data.symbol)
            if existing_pending_case:
                logger.info(f"[{alert_data.symbol}] Nowy alert ({alert_id}) unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). Usuwam.")
                state_manager.delete_case_by_id(existing_pending_case.id)

            if _validate_alert_logic(alert_data):
                logger.info(f"[{alert_data.symbol}] Alert ({alert_id}) przeszedł walidację. Tworzę teczkę PENDING.")
                new_case = AnalyticalCase(
                    alert_id=alert_data.id,
                    symbol=alert_data.symbol,
                    alert_data=alert_data.model_dump(by_alias=True)
                )
                state_manager.create_analytical_case(new_case)
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ({alert_id}): {e}", exc_info=True)

def _handle_pending_case(case_doc_snapshot: Any, kline: Kline):
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    entry_price = alert.entry

    entry_triggered = False
    if alert.direction == 'LONG' and kline.low <= entry_price:
        entry_triggered = True
    elif alert.direction == 'SHORT' and kline.high >= entry_price:
        entry_triggered = True
        
    if entry_triggered:
        logger.info(f"--- [TRIGGER] --- [{alert.symbol}] | ID: {case_id} | Cena wejścia {entry_price} dotknięta.")
        updates = {"status": "TRIGGERED", "triggered_at": datetime.now(timezone.utc)}
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
    base_log_data = {
        "analysis_id": case_id, "symbol": symbol, "direction": direction,
        "entry_price": alert.entry, "sl_price": sl_price,
        "timestamp_alert": alert.received_at, "timestamp_entry": case_doc.get('triggered_at'),
        "timestamp_close": close_timestamp, "raw_alert_data": json.dumps(alert.model_dump(by_alias=True))
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

def run_analysis_cycle():
    """Główna, zintegrowana pętla logiki bota."""
    logger.info("--- ROZPOCZYNAM ZINTEGROWANY CYKL ANALITYCZNY ---")

    # ETAP 1: Pobierz i przetwórz nowe alerty, aby utworzyć nowe teczki
    last_ts = load_last_processed_timestamp()
    new_alerts, new_ts = fetch_new_alerts_since(last_ts)
    if new_alerts:
        _process_new_alerts(new_alerts)
        if new_ts and (not last_ts or new_ts > last_ts):
            save_last_processed_timestamp(new_ts)

    # ETAP 2: Pobierz WSZYSTKIE teczki (stare i te nowo utworzone) do analizy
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return

    # ETAP 3: Pobierz potrzebne dane rynkowe
    logger.info(f"Znaleziono {len(all_cases_docs)} teczek analitycznych do przetworzenia.")
    symbols_to_watch = {doc.to_dict().get('symbol') for doc in all_cases_docs if doc.to_dict()}
    if not symbols_to_watch:
        logger.info("Brak symboli do monitorowania w aktywnych teczkach.")
        return

    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(symbols_to_watch))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych kline z cache'u. Pomijam analizę w tym cyklu.")
        return

    klines_models = {symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()}

    # ETAP 4: Przeanalizuj każdą teczkę
    logger.info(f"Rozpoczynam analizę {len(all_cases_docs)} teczek na podstawie {len(klines_models)} świec.")
    for case_doc_snapshot in all_cases_docs:
        case_id = case_doc_snapshot.id
        try:
            case_doc = case_doc_snapshot.to_dict()
            symbol = case_doc.get('symbol')
            status = case_doc.get('status')
            
            latest_kline = klines_models.get(symbol)
            if not latest_kline:
                logger.warning(f"Brak danych kline dla symbolu {symbol} (teczka {case_id}). Pomijam.")
                continue
            
            if status == 'PENDING':
                _handle_pending_case(case_doc_snapshot, latest_kline)
            elif status == 'TRIGGERED':
                _handle_triggered_case(case_doc_snapshot, latest_kline)
        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania teczki {case_id}: {e}", exc_info=True)

    logger.info("--- ZAKOŃCZONO ZINTEGROWANY CYKL ANALITYCZNY ---")