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

def _correct_and_validate_alert(alert: AlertData) -> Optional[AlertData]:
    """
    Sprawdza, koryguje i waliduje alert. Zwraca skorygowany alert lub None, jeśli jest nieprawidłowy.
    """
    # KROK 1: Logika autonaprawy dla odwróconych wartości entry/sl
    if alert.direction == 'SHORT' and alert.entry > alert.sl:
        logger.warning(
            f"[{alert.symbol}] Wykryto i skorygowano odwrócone wartości entry/sl dla alertu SHORT. "
            f"Oryginalnie: entry={alert.entry}, sl={alert.sl}. "
            f"Po korekcie: entry={alert.sl}, sl={alert.entry}."
        )
        alert.entry, alert.sl = alert.sl, alert.entry
    elif alert.direction == 'LONG' and alert.entry < alert.sl:
        logger.warning(
            f"[{alert.symbol}] Wykryto i skorygowano odwrócone wartości entry/sl dla alertu LONG. "
            f"Oryginalnie: entry={alert.entry}, sl={alert.sl}. "
            f"Po korekcie: entry={alert.sl}, sl={alert.entry}."
        )
        alert.entry, alert.sl = alert.sl, alert.entry

    # KROK 2: Finalna walidacja (po ewentualnej korekcie)
    if alert.direction == 'LONG' and alert.sl >= alert.entry:
        logger.warning(f"Odrzucono alert LONG [{alert.symbol}]: SL ({alert.sl}) >= Entry ({alert.entry}). Alert jest niehandlowalny.")
        return None
    if alert.direction == 'SHORT' and alert.sl <= alert.entry:
        logger.warning(f"Odrzucono alert SHORT [{alert.symbol}]: SL ({alert.sl}) <= Entry ({alert.entry}). Alert jest niehandlowalny.")
        return None
    
    risk_distance = abs(alert.entry - alert.sl)
    if alert.entry > 0:
        min_risk_percentage = 0.0005  # 0.05%
        if (risk_distance / alert.entry) < min_risk_percentage:
            logger.warning(f"Odrzucono alert [{alert.symbol}]: Ryzyko poniżej {min_risk_percentage*100}%.")
            return None
    else: # Obsługa przypadku, gdy cena wejścia jest 0
        logger.warning(f"Odrzucono alert [{alert.symbol}]: Cena wejścia wynosi 0.")
        return None
        
    return alert

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    """Przetwarza nowe alerty, bezwzględnie stosując regułę zastępowania, a następnie waliduje i tworzy nowe teczki."""
    if not newly_fetched_alerts:
        return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    
    for alert_dict in newly_fetched_alerts:
        try:
            alert_data = AlertData.model_validate(alert_dict)
            
            # ====================================================================
            # === KLUCZOWA ZMIANA: NAJPIERW ZASTĘPOWANIE, POTEM WALIDACJA ===
            # ====================================================================
            
            # KROK 1: Bezwzględne zastosowanie reguły zastępowania.
            # Nowy alert ZAWSZE unieważnia stary setup PENDING.
            existing_pending_case = state_manager.get_pending_case_for_symbol(alert_data.symbol)
            if existing_pending_case:
                logger.info(
                    f"[{alert_data.symbol}] Nowy alert unieważnia istniejącą teczkę PENDING ({existing_pending_case.id}). "
                    f"Usuwam starą teczkę."
                )
                state_manager.delete_case_by_id(existing_pending_case.id)

            # KROK 2: Dopiero teraz próbujemy przetworzyć nowy alert.
            validated_alert = _correct_and_validate_alert(alert_data)
            
            if validated_alert:
                # Jeśli nowy alert jest poprawny, tworzymy dla niego nową teczkę.
                new_case = AnalyticalCase(
                    alert_id=validated_alert.id,
                    symbol=validated_alert.symbol,
                    alert_data=validated_alert.model_dump(by_alias=True)
                )
                state_manager.create_analytical_case(new_case)
            else:
                # Jeśli nowy alert jest niepoprawny, logujemy to i kończymy (stara teczka już została usunięta).
                logger.info(f"[{alert_data.symbol}] Nowy alert został odrzucony po walidacji. Nie tworzę nowej teczki PENDING.")

        except ValidationError as e:
            logger.error(f"Błąd walidacji Pydantic dla alertu: {e}", extra={"json_fields": {"alert_id": alert_dict.get('id')}})
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu: {e}", exc_info=True, extra={"json_fields": {"alert_id": alert_dict.get('id')}})

# --- Faza 2 i 4: Zarządzanie Cyklem Życia Teczek (BEZ ZMIAN) ---
# ... (reszta pliku, czyli funkcje _handle_pending_case, _handle_triggered_case, run_analysis_cycle, pozostaje identyczna) ...
def _handle_pending_case(case_doc: Any, kline: Kline):
    """Sprawdza warunek wejścia dla teczki PENDING."""
    alert = AlertData.model_validate(case_doc.get('alert_data'))
    entry_price = alert.entry
    
    entry_triggered = False
    if alert.direction == 'LONG' and kline.low <= entry_price:
        entry_triggered = True
    elif alert.direction == 'SHORT' and kline.high >= entry_price:
        entry_triggered = True
        
    if entry_triggered:
        logger.info(f"--- [TRIGGER] --- [{alert.symbol}] | ID: {case_doc.id} | Cena wejścia {entry_price} dotknięta.")
        updates = {
            "status": "TRIGGERED",
            "triggered_at": datetime.now(timezone.utc)
        }
        state_manager.update_case_status_and_results(case_doc.id, updates)

def _handle_triggered_case(case_doc: Any, kline: Kline):
    """Sprawdza warunki SL/TP dla teczki TRIGGERED i zapisuje wyniki."""
    case_id = case_doc.id
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

    # Hierarchia: Stop Loss ma najwyższy priorytet
    if sl_hit:
        logger.info(f"--- [SL HIT] --- [{symbol}] | ID: {case_id} | Wszystkie nierozstrzygnięte scenariusze = LOSE.")
        for target_level in unresolved_targets:
            target_price = getattr(alert, target_level)
            log_analysis_result({
                "analysis_id": case_id, "symbol": symbol, "direction": direction,
                "entry_price": alert.entry, "sl_price": sl_price,
                "target_level": target_level, "target_price": target_price,
                "result": "LOSE", "timestamp_alert": alert.received_at,
                "timestamp_entry": case_doc.get('triggered_at'),
                "timestamp_close": close_timestamp,
                "raw_alert_data": json.dumps(alert.model_dump(by_alias=True))
            })
            resolved_scenarios[f'results.{target_level}'] = "LOSE"
    else:
        # Hierarchia: Take Profit (jeśli SL nie został trafiony)
        for target_level in unresolved_targets:
            target_price = getattr(alert, target_level)
            tp_hit = (direction == 'LONG' and kline.high >= target_price) or \
                     (direction == 'SHORT' and kline.low <= target_price)
            
            if tp_hit:
                logger.info(f"--- [TP HIT] --- [{symbol}] | ID: {case_id} | Scenariusz {target_level} = WIN.")
                log_analysis_result({
                    "analysis_id": case_id, "symbol": symbol, "direction": direction,
                    "entry_price": alert.entry, "sl_price": sl_price,
                    "target_level": target_level, "target_price": target_price,
                    "result": "WIN", "timestamp_alert": alert.received_at,
                    "timestamp_entry": case_doc.get('triggered_at'),
                    "timestamp_close": close_timestamp,
                    "raw_alert_data": json.dumps(alert.model_dump(by_alias=True))
                })
                resolved_scenarios[f'results.{target_level}'] = "WIN"

    if resolved_scenarios:
        state_manager.update_case_status_and_results(case_id, resolved_scenarios)
        
        final_results_count = len(results)
        resolved_count = sum(1 for v in results.values() if v != "UNRESOLVED") + len(resolved_scenarios)
        if resolved_count >= final_results_count:
            logger.info(f"[{case_id}] Wszystkie 6 scenariuszy rozstrzygnięte. Finalne usunięcie teczki.")
            state_manager.delete_case_by_id(case_id)

def run_analysis_cycle():
    """Główna pętla logiki analitycznej."""
    logger.info("Rozpoczynam główną pętlę cyklu analitycznego.")
    
    all_cases_docs = list(state_manager.get_all_analytical_cases())
    if not all_cases_docs:
        logger.info("Brak aktywnych teczek analitycznych. Kończę cykl.")
        return

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

    for case_doc_snapshot in all_cases_docs:
        case_doc = case_doc_snapshot.to_dict()
        case_id = case_doc_snapshot.id
        symbol = case_doc.get('symbol')
        status = case_doc.get('status')
        
        latest_kline = klines_models.get(symbol)
        if not latest_kline:
            logger.warning(f"Brak danych kline dla symbolu {symbol} (teczka {case_id}).")
            continue
        
        try:
            if status == 'PENDING':
                _handle_pending_case(case_doc, latest_kline)
            elif status == 'TRIGGERED':
                _handle_triggered_case(case_doc, latest_kline)
        except Exception as e:
            logger.error(f"Błąd podczas przetwarzania teczki {case_id} dla {symbol}: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę cyklu analitycznego.")