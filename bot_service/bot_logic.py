# Lokalizacja: bot_service/bot_logic.py

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import ValidationError

from shared_lib.models import AlertData, Kline, AnalyticalCase
from bot_service import state_manager
from bot_service.bigquery_logger import log_analysis_result

logger = logging.getLogger(__name__)

def create_analytical_case(case_data: AnalyticalCase):
    """Tworzy nowy dokument teczki analitycznej w Firestore."""
    case_id = case_data.alert_id
    symbol = case_data.symbol
    logger.info(f"[{case_id}][{symbol}] Próba utworzenia dokumentu w kolekcji '{constants.ANALYTICAL_CASES_COLLECTION}'...")
    try:
        db = _get_db()
        doc_ref = db.collection(constants.ANALYTICAL_CASES_COLLECTION).document(case_id)
        
        # Używamy json.loads(model.json()) aby uzyskać słownik z poprawnymi typami dla Firestore
        data_to_set = json.loads(case_data.json())
        
        doc_ref.set(data_to_set)
        
        logger.info(f"[{case_id}][{symbol}] SUKCES! Pomyślnie utworzono teczkę analityczną.")
    except Exception as e:
        # TO JEST KLUCZOWY LOG, KTÓREGO SZUKAMY
        logger.critical(
            f"[{case_id}][{symbol}] KRYTYCZNY BŁĄD podczas zapisu do Firestore w create_analytical_case: {e}", 
            exc_info=True
        )
        # Rzucamy wyjątek dalej, aby zatrzymać proces, jeśli zapis się nie powiedzie
        raise

def _correct_and_validate_alert(alert: AlertData) -> Optional[AlertData]:
    """Koryguje i waliduje alert, zachowując minimalny próg ryzyka."""
    # Autonaprawa
    if alert.direction == 'SHORT' and alert.entry > alert.sl:
        logger.warning(f"[{alert.symbol}] Skorygowano odwrócone entry/sl dla SHORT.")
        alert.entry, alert.sl = alert.sl, alert.entry
    elif alert.direction == 'LONG' and alert.entry < alert.sl:
        logger.warning(f"[{alert.symbol}] Skorygowano odwrócone entry/sl dla LONG.")
        alert.entry, alert.sl = alert.sl, alert.entry

    # Walidacja logiczna
    if (alert.direction == 'LONG' and alert.sl >= alert.entry) or \
       (alert.direction == 'SHORT' and alert.sl <= alert.entry):
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Pozycja niehandlowalna (sl={alert.sl}, entry={alert.entry}).")
        return None
    
    # Walidacja minimalnego ryzyka (zostawiamy niski próg, aby zbierać dane)
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None or risk_perc < 0.05:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej absolutnego minimum 0.05%.")
        return None
        
    return alert

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    """Przetwarza nowe alerty, stosując regułę zastępowania, a następnie waliduje i tworzy teczki."""
    if not newly_fetched_alerts: return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    
    for alert_dict in newly_fetched_alerts:
        try:
            alert_data = AlertData.model_validate(alert_dict)
            
            existing_pending_case = state_manager.get_pending_case_for_symbol(alert_data.symbol)
            if existing_pending_case:
                logger.info(f"[{alert_data.symbol}] Nowy alert unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). Usuwam.")
                state_manager.delete_case_by_id(existing_pending_case.id)

            validated_alert = _correct_and_validate_alert(alert_data)
            
            if validated_alert:
                new_case = AnalyticalCase(
                    alert_id=validated_alert.id,
                    symbol=validated_alert.symbol,
                    alert_data=validated_alert.model_dump(by_alias=True)
                )
                state_manager.create_analytical_case(new_case)
            else:
                logger.info(f"[{alert_data.symbol}] Nowy alert odrzucony po walidacji. Nie tworzę nowej teczki.")

        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania alertu {alert_dict.get('id')}: {e}", exc_info=True)

def _handle_pending_case(case_doc: Any, kline: Kline):
    # ... (ta funkcja pozostaje bez zmian) ...
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    entry_price = alert.entry
    entry_triggered = (alert.direction == 'LONG' and kline.low <= entry_price) or \
                      (alert.direction == 'SHORT' and kline.high >= entry_price)
    if entry_triggered:
        logger.info(f"--- [TRIGGER] --- [{alert.symbol}] | ID: {case_doc.id} | Cena wejścia {entry_price} dotknięta.")
        updates = {"status": "TRIGGERED", "triggered_at": datetime.now(timezone.utc)}
        state_manager.update_case_status_and_results(case_doc.id, updates)

def _handle_triggered_case(case_doc: Any, kline: Kline):
    # ... (reszta funkcji bez zmian, ale dodajemy obliczenia) ...
    case_id, symbol = case_doc.id, case_doc.get('symbol')
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    results = case_doc.get('results', {})
    
    unresolved_targets = {k: v for k, v in results.items() if v == "UNRESOLVED"}
    if not unresolved_targets:
        state_manager.delete_case_by_id(case_id)
        return

    sl_price, direction = alert.sl, alert.direction
    sl_hit = (direction == 'LONG' and kline.low <= sl_price) or \
             (direction == 'SHORT' and kline.high >= sl_price)

    resolved_scenarios = {}
    close_timestamp = datetime.fromtimestamp(kline.timestamp / 1000, tz=timezone.utc)
    
    # --- OBLICZENIE RYZYKA DLA TEGO ALERTU ---
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)

    # Przygotowanie danych bazowych do logu
    base_log_data = {
        "analysis_id": case_id, "symbol": symbol, "direction": direction,
        "entry_price": alert.entry, "sl_price": sl_price,
        "timestamp_alert": alert.received_at, "timestamp_entry": case_doc.get('triggered_at'),
        "timestamp_close": close_timestamp, "raw_alert_data": json.dumps(alert.model_dump(by_alias=True)),
        "risk_percentage": risk_perc  # <-- DODANE NOWE POLE
    }

    if sl_hit:
        logger.info(f"--- [SL HIT] --- [{symbol}] | ID: {case_id} | Wszystkie nierozstrzygnięte scenariusze = LOSE.")
        for target_level in unresolved_targets:
            log_data = base_log_data.copy()
            log_data.update({
                "target_level": target_level,
                "target_price": getattr(alert, target_level),
                "result": "LOSE"
            })
            log_analysis_result(log_data)
            resolved_scenarios[f'results.{target_level}'] = "LOSE"
    else:
        for target_level in unresolved_targets:
            target_price = getattr(alert, target_level)
            tp_hit = (direction == 'LONG' and kline.high >= target_price) or \
                     (direction == 'SHORT' and kline.low <= target_price)
            if tp_hit:
                logger.info(f"--- [TP HIT] --- [{symbol}] | ID: {case_id} | Scenariusz {target_level} = WIN.")
                log_data = base_log_data.copy()
                log_data.update({
                    "target_level": target_level,
                    "target_price": target_price,
                    "result": "WIN"
                })
                log_analysis_result(log_data)
                resolved_scenarios[f'results.{target_level}'] = "WIN"

    if resolved_scenarios:
        state_manager.update_case_status_and_results(case_id, resolved_scenarios)
        # Sprawdzenie po aktualizacji, czy teczka jest w pełni rozstrzygnięta
        if len(results) - len(unresolved_targets) + len(resolved_scenarios) >= 6:
            logger.info(f"[{case_id}] Wszystkie 6 scenariuszy rozstrzygnięte. Finalne usunięcie teczki.")
            state_manager.delete_case_by_id(case_id)

def run_analysis_cycle():
    # ... (ta funkcja pozostaje bez zmian) ...
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return
    symbols_to_watch = {doc.to_dict().get('symbol') for doc in all_cases_docs}
    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(filter(None, symbols_to_watch)))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych kline z cache'u. Pomijam cykl.")
        return
    klines_models = {s: Kline.model_validate(d) for s, d in klines_data_from_cache.items()}
    for case_doc_snapshot in all_cases_docs:
        case_doc = case_doc_snapshot.to_dict()
        symbol, status = case_doc.get('symbol'), case_doc.get('status')
        if not (kline := klines_models.get(symbol)):
            continue
        try:
            if status == 'PENDING': _handle_pending_case(case_doc, kline)
            elif status == 'TRIGGERED': _handle_triggered_case(case_doc, kline)
        except Exception as e:
            logger.error(f"Błąd przetwarzania teczki {case_doc_snapshot.id}: {e}", exc_info=True)
    logger.info("Zakończono główną pętlę cyklu analitycznego.")