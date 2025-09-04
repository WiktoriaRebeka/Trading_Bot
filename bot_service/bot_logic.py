import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import ValidationError

from shared_lib.models import AlertData, Kline, AnalyticalCase
from bot_service import state_manager
from bot_service.bigquery_logger import log_analysis_result

logger = logging.getLogger(__name__)

# --- Faza 1: Przetwarzanie Nowych Alertów i Tworzenie Teczek PENDING ---

def _calculate_risk_percentage(entry_price: float, sl_price: float) -> Optional[float]:
    """Oblicza procentową odległość SL od ceny wejścia."""
    if entry_price == 0:
        return None
    risk_distance = abs(entry_price - sl_price)
    return round((risk_distance / entry_price) * 100, 4)

def _validate_and_correct_alert(alert: AlertData) -> Optional[AlertData]:
    """
    Waliduje alert. Jeśli jest odwrócony, próbuje go naprawić.
    Zwraca poprawny obiekt AlertData lub None.
    """
    original_entry, original_sl = alert.entry, alert.sl
    is_corrected = False

    # Sprawdzenie, czy alert jest odwrócony
    if (alert.direction == 'SHORT' and original_entry > original_sl) or \
       (alert.direction == 'LONG' and original_entry < original_sl):
        logger.warning(
            f"[{alert.symbol}] Wykryto odwrócone wartości entry/sl. Próba korekty. "
            f"Oryginalnie: entry={original_entry}, sl={original_sl}."
        )
        entry, sl = original_sl, original_entry
        is_corrected = True
    else:
        entry, sl = original_entry, original_sl

    # Walidacja logiczna (na potencjalnie skorygowanych wartościach)
    if (alert.direction == 'LONG' and sl >= entry) or \
       (alert.direction == 'SHORT' and sl <= entry):
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Pozycja niehandlowalna (entry={entry}, sl={sl}).")
        return None
    
    # Walidacja minimalnego ryzyka
    risk_perc = _calculate_risk_percentage(entry, sl)
    if risk_perc is None or risk_perc < 0.05:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej minimum 0.05% (wynosi {risk_perc}%).")
        return None
    
    # Jeśli alert został skorygowany, tworzymy nowy, poprawny obiekt
    if is_corrected:
        corrected_alert_data = alert.model_dump(by_alias=True) # Używamy by_alias=True dla spójności
        corrected_alert_data['entry'] = entry
        corrected_alert_data['sl'] = sl
        return AlertData.model_validate(corrected_alert_data)
    
    return alert

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    """Przetwarza nowe alerty, bezwzględnie stosując regułę zastępowania, a następnie waliduje i tworzy nowe teczki."""
    if not newly_fetched_alerts:
        return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'unknown')
        try:
            raw_alert = AlertData.model_validate(alert_dict)
            
            existing_pending_case = state_manager.get_pending_case_for_symbol(raw_alert.symbol)
            if existing_pending_case:
                logger.info(
                    f"[{raw_alert.symbol}] Nowy alert ({alert_id}) unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). Usuwam."
                )
                state_manager.delete_case_by_id(existing_pending_case.id)

            validated_alert = _validate_and_correct_alert(raw_alert)
            
            if validated_alert:
                logger.info(f"[{validated_alert.symbol}] Alert ({alert_id}) przeszedł walidację. Tworzę teczkę PENDING.")
                new_case = AnalyticalCase(
                    alert_id=validated_alert.id,
                    symbol=validated_alert.symbol,
                    alert_data=validated_alert.model_dump(by_alias=True)
                )
                state_manager.create_analytical_case(new_case)
            else:
                logger.info(f"[{raw_alert.symbol}] Nowy alert ({alert_id}) został odrzucony po walidacji. Nie tworzę nowej teczki PENDING.")

        except ValidationError as e:
            logger.error(f"Błąd walidacji Pydantic dla alertu ({alert_id}): {e}", extra={"json_fields": {"alert_id": alert_id}})
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ({alert_id}): {e}", exc_info=True, extra={"json_fields": {"alert_id": alert_id}})

# --- Faza 2 i 4: Zarządzanie Cyklem Życia Teczek ---

def _handle_pending_case(case_doc_snapshot: Any, kline: Kline):
    """Sprawdza warunek wejścia dla teczki PENDING."""
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
    """Sprawdza warunki SL/TP dla teczki TRIGGERED i zapisuje wyniki."""
    case_doc = case_doc_snapshot.to_dict()
    case_id = case_doc_snapshot.id
    symbol = case_doc.get('symbol')
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    results = case_doc.get('results', {})
    
    unresolved_targets = {k: v for k, v in results.items() if v == "UNRESOLVED"}
    if not unresolved_targets:
        logger.info(f"[{case_id}] Wszystkie scenariusze rozstrzygnięte. Oznaczam do usunięcia.")
        state_manager.delete_case_by_id(case_id)
        return

    sl_price = alert.sl
    direction = alert.direction
    
    sl_hit = (direction == 'LONG' and kline.low <= sl_price) or \
             (direction == 'SHORT' and kline.high >= sl_price)

    resolved_scenarios = {}
    close_timestamp = datetime.fromtimestamp(kline.timestamp / 1000, tz=timezone.utc)
    
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)

    base_log_data = {
        "analysis_id": case_id, "symbol": symbol, "direction": direction,
        "entry_price": alert.entry, "sl_price": sl_price,
        "timestamp_alert": alert.received_at, "timestamp_entry": case_doc.get('triggered_at'),
        "timestamp_close": close_timestamp, "raw_alert_data": json.dumps(alert.model_dump(by_alias=True)),
        "risk_percentage": risk_perc
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
        
        if len(results) - len(unresolved_targets) + len(resolved_scenarios) >= 6:
            logger.info(f"[{case_id}] Wszystkie 6 scenariuszy rozstrzygnięte. Finalne usunięcie teczki.")
            state_manager.delete_case_by_id(case_id)

def run_analysis_cycle():
    """Główna pętla logiki analitycznej."""
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return

    logger.info(f"[DIAGNOSTYKA] Znaleziono {len(all_cases_docs)} teczek analitycznych do przetworzenia.")
    symbols_to_watch = {doc.to_dict().get('symbol') for doc in all_cases_docs}
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