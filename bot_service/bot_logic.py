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

def _correct_and_validate_alert(alert: AlertData) -> bool:
    """
    Koryguje alert w miejscu i waliduje go. Zwraca True, jeśli jest poprawny, False w przeciwnym razie.
    """
    # KROK 1: Logika autonaprawy dla odwróconych wartości entry/sl (modyfikacja w miejscu)
    if alert.direction == 'SHORT' and alert.entry > alert.sl:
        logger.warning(
            f"[{alert.symbol}] Wykryto i skorygowano odwrócone wartości entry/sl dla alertu SHORT. "
            f"Oryginalnie: entry={alert.entry}, sl={alert.sl}."
        )
        alert.entry, alert.sl = alert.sl, alert.entry
    elif alert.direction == 'LONG' and alert.entry < alert.sl:
        logger.warning(
            f"[{alert.symbol}] Wykryto i skorygowano odwrócone wartości entry/sl dla alertu LONG. "
            f"Oryginalnie: entry={alert.entry}, sl={alert.sl}."
        )
        alert.entry, alert.sl = alert.sl, alert.entry

    # KROK 2: Finalna walidacja (na potencjalnie zmodyfikowanym obiekcie)
    if (alert.direction == 'LONG' and alert.sl >= alert.entry) or \
       (alert.direction == 'SHORT' and alert.sl <= alert.entry):
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Pozycja niehandlowalna (entry={alert.entry}, sl={alert.sl}).")
        return False
    
    risk_perc = _calculate_risk_percentage(alert.entry, alert.sl)
    if risk_perc is None or risk_perc < 0.05:
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej minimum 0.05% (wynosi {risk_perc}%).")
        return False
        
    return True

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    """Przetwarza nowe alerty, bezwzględnie stosując regułę zastępowania, a następnie waliduje i tworzy nowe teczki."""
    if not newly_fetched_alerts:
        return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")

    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'unknown')
        if alert_id == 'unknown':
            logger.error("Otrzymano alert bez ID. Pomijam.", extra={"json_fields": {"alert_data": alert_dict}})
            continue

        try:
            # Krok 1: Walidacja danych przychodzących do modelu AlertData
            alert_data_model = AlertData.model_validate(alert_dict)
            
            # Krok 2: Sprawdzenie i usunięcie istniejącej teczki PENDING (reguła zastępowania)
            existing_pending_case = state_manager.get_pending_case_for_symbol(alert_data_model.symbol)
            if existing_pending_case:
                logger.info(
                    f"[{alert_data_model.symbol}] Nowy alert ({alert_id}) unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). Usuwam."
                )
                state_manager.delete_case_by_id(existing_pending_case.id)

            # Krok 3: Walidacja logiki biznesowej (ryzyko, poprawność SL/Entry)
            # Ważne: przekazujemy model Pydantic, który może być modyfikowany wewnątrz funkcji
            is_valid = _correct_and_validate_alert(alert_data_model)
            
            if is_valid:
                logger.info(f"[{alert_data_model.symbol}] Alert ({alert_id}) przeszedł walidację. Tworzę teczkę PENDING.")
                
                # --- KLUCZOWA POPRAWKA ---
                # Tworzymy obiekt AnalyticalCase, jawnie mapując pola.
                # Cały oryginalny słownik alertu (już zwalidowany) trafia do pola `alert_data`.
                new_case = AnalyticalCase(
                    alert_id=alert_data_model.id,
                    symbol=alert_data_model.symbol,
                    alert_data=alert_data_model.model_dump(by_alias=True) # Używamy danych ze zwalidowanego i potencjalnie skorygowanego modelu
                )
                state_manager.create_analytical_case(new_case)
            else:
                logger.warning(f"[{alert_data_model.symbol}] Nowy alert ({alert_id}) został odrzucony po walidacji. Nie tworzę nowej teczki PENDING.")

        except ValidationError as e:
            logger.error(f"Błąd walidacji Pydantic dla alertu ({alert_id}): {e}", extra={"json_fields": {"alert_id": alert_id, "alert_data": alert_dict}})
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ({alert_id}): {e}", exc_info=True, extra={"json_fields": {"alert_id": alert_id}})

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
                _handle_pending_case(case_doc_snapshot, kline)
            elif status == 'TRIGGERED':
                _handle_triggered_case(case_doc_snapshot, kline)
        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania teczki {case_id}: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę cyklu analitycznego.")