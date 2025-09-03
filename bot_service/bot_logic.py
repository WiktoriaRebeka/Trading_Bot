# Lokalizacja: bot_service/bot_logic.py

import logging
from decimal import Decimal
from typing import Any, Dict, List

from bot_service import state_manager, analyzer
from bot_service.bybit_executor import BybitExecutor
from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

def _is_sl_valid(alert_data: AlertData) -> bool:
    """Sprawdza, czy Stop Loss jest po właściwej stronie ceny wejścia."""
    if alert_data.direction == "LONG" and alert_data.sl >= alert_data.entry:
        logger.error(f"[{alert_data.symbol}] BŁĄD LOGIKI: Stop Loss ({alert_data.sl}) jest powyżej lub równy cenie wejścia ({alert_data.entry}) dla pozycji LONG.")
        return False
    if alert_data.direction == "SHORT" and alert_data.sl <= alert_data.entry:
        logger.error(f"[{alert_data.symbol}] BŁĄD LOGIKI: Stop Loss ({alert_data.sl}) jest poniżej lub równy cenie wejścia ({alert_data.entry}) dla pozycji SHORT.")
        return False
    return True

# Zastąp funkcję process_new_alerts
def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not newly_fetched_alerts:
        return
    logger.info(f"Otrzymano {len(newly_fetched_alerts)} nowych alertów do przetworzenia.")
    
    # Grupujemy alerty po symbolach, aby przetwarzać je w odpowiedniej kolejności
    alerts_by_symbol = {}
    for alert in newly_fetched_alerts:
        symbol = alert.get('symbol')
        if symbol:
            if symbol not in alerts_by_symbol:
                alerts_by_symbol[symbol] = []
            alerts_by_symbol[symbol].append(alert)

    for symbol, alerts in alerts_by_symbol.items():
        # Pobieramy wszystkie istniejące scenariusze dla danego symbolu
        existing_scenarios = state_manager.find_all_scenarios_by_symbol(symbol)
        
        for alert_dict in alerts:
            alert_id = alert_dict.get('id', 'N/A')
            try:
                alert_data = AlertData.model_validate(alert_dict)

                # Sprawdzamy, czy istnieje scenariusz w stanie PENDING
                pending_scenario = next((s for s in existing_scenarios if s.entry_status == 'PENDING'), None)

                if pending_scenario:
                    # ZLECENIE OCZEKUJĄCE: Anulujemy starą analizę i zastępujemy ją nową
                    logger.info(f"[{symbol}] Znaleziono 'oczekującą' analizę ({pending_scenario.alert_id}). Zastępuję ją nowym alertem {alert_id}.")
                    state_manager.delete_analytical_scenario(pending_scenario.alert_id)
                    # Usuwamy go z naszej listy, aby nie był brany pod uwagę przy następnym alercie
                    existing_scenarios.remove(pending_scenario)
                
                # Walidacje
                if not _is_sl_valid(alert_data):
                    logger.warning(f"[{symbol}] Alert {alert_id} odrzucony (nieprawidłowy SL).")
                    continue
                
                MIN_SL_DISTANCE_PERCENT = Decimal("0.0005")
                entry_price = Decimal(str(alert_data.entry))
                sl_price = Decimal(str(alert_data.sl))
                if entry_price > 0 and (abs(entry_price - sl_price) / entry_price) < MIN_SL_DISTANCE_PERCENT:
                    logger.warning(f"[{symbol}] Alert {alert_id} odrzucony (zbyt mała odległość SL).")
                    continue
                
                logger.info(f"[{symbol}] Alert {alert_id} przeszedł walidację. Tworzę nową 'teczkę analityczną'.")
                state_manager.create_analytical_scenario(alert_data)

            except Exception as e:
                logger.critical(f"[Alert: {alert_id}] Błąd w process_new_alerts: {e}", exc_info=True)

def run_trading_logic(bybit_executor: BybitExecutor):
    """
    W Trybie Analitycznym, ta funkcja jest odpowiedzialna wyłącznie za uruchomienie
    cyklu analizy.
    """
    analyzer.run_analysis_cycle()

# Ta funkcja pozostaje wyłączona
def sync_pnl_history(bybit_executor: BybitExecutor):
    logger.info("--- Pętla `sync_pnl_history` jest wyłączona (Tryb Analityczny). ---")
    pass