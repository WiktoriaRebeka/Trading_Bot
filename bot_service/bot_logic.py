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

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not newly_fetched_alerts:
        return
    logger.info(f"Otrzymano {len(newly_fetched_alerts)} nowych alertów do przetworzenia.")
    
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'N/A')
        try:
            alert_data = AlertData.model_validate(alert_dict)
            symbol = alert_data.symbol

            # --- NOWA, ZAAWANSOWANA LOGIKA OBSŁUGI ISTNIEJĄCEJ ANALIZY ---
            existing_scenario = state_manager.find_scenario_by_symbol(symbol)

            if existing_scenario:
                # Sprawdzamy, czy jakikolwiek scenariusz osiągnął już WIN (odpowiednik "pozycji otwartej")
                is_position_open = any(status == 'WIN' for status in existing_scenario.scenario_status.values())

                if is_position_open:
                    # POZYCJA OTWARTA: Ignorujemy nowy alert i kontynuujemy starą analizę
                    logger.warning(f"[{symbol}] Istnieje już 'otwarta' analiza (przynajmniej jeden TP trafiony). Ignoruję nowy alert {alert_id}.")
                    continue
                else:
                    # ZLECENIE OCZEKUJĄCE: Anulujemy starą analizę, aby zrobić miejsce na nową
                    logger.info(f"[{symbol}] Znaleziono 'oczekującą' analizę ({existing_scenario.alert_id}). Zastępuję ją nowym alertem {alert_id}.")
                    state_manager.delete_analytical_scenario(existing_scenario.alert_id)
                    # Nie usuwamy 'active_setup', bo zostanie on nadpisany poniżej
            
            # --- Walidacje (pozostają bez zmian) ---
            if not _is_sl_valid(alert_data):
                logger.warning(f"[{symbol}] Alert {alert_id} odrzucony z powodu nieprawidłowej logiki Stop Lossa.")
                continue

            MIN_SL_DISTANCE_PERCENT = Decimal("0.0005")
            entry_price = Decimal(str(alert_data.entry))
            sl_price = Decimal(str(alert_data.sl))

            if entry_price > 0:
                sl_distance_percentage = abs(entry_price - sl_price) / entry_price
                if sl_distance_percentage < MIN_SL_DISTANCE_PERCENT:
                    logger.warning(
                        f"[{symbol}] Alert {alert_id} odrzucony. Odległość SL ({sl_distance_percentage:.4%}) jest mniejsza niż minimum.")
                    continue
            
            logger.info(f"[{symbol}] Alert {alert_id} przeszedł walidację. Inicjuję proces analityczny.")
            # Tworzymy nową "teczkę analityczną" i nadpisujemy/tworzymy blokadę
            state_manager.create_analytical_scenario(alert_data)
            state_manager.create_setup_from_alert(alert_data)

        except Exception as e:
            logger.critical(f"[Alert: {alert_id}] Nieoczekiwany błąd w process_new_alerts: {e}", exc_info=True)

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