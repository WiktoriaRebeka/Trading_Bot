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

            # ZASADA 1: Jeden Symbol, Jedna Operacja
            if state_manager.get_active_setup(symbol):
                logger.warning(f"[{symbol}] Już istnieje aktywna analiza dla tego symbolu. Ignoruję nowy alert {alert_id}.")
                continue

            # ZASADA 2: Walidacja Logiki SL
            if not _is_sl_valid(alert_data):
                logger.warning(f"[{symbol}] Alert {alert_id} odrzucony z powodu nieprawidłowej logiki Stop Lossa.")
                continue

            # ZASADA 3: Filtr Minimalnego Ryzyka
            MIN_SL_DISTANCE_PERCENT = Decimal("0.0005") # 0.05%
            entry_price = Decimal(str(alert_data.entry))
            sl_price = Decimal(str(alert_data.sl))

            if entry_price > 0:
                sl_distance_percentage = abs(entry_price - sl_price) / entry_price
                if sl_distance_percentage < MIN_SL_DISTANCE_PERCENT:
                    logger.warning(
                        f"[{symbol}] Alert {alert_id} odrzucony. Odległość SL ({sl_distance_percentage:.4%}) "
                        f"jest mniejsza niż wymagane minimum ({MIN_SL_DISTANCE_PERCENT:.4%})."
                    )
                    continue
            
            logger.info(f"[{symbol}] Alert {alert_id} przeszedł walidację. Inicjuję proces analityczny.")
            # Krok 1: Tworzymy "teczkę analityczną"
            state_manager.create_analytical_scenario(alert_data)
            
            # Krok 2: Tworzymy blokadę w 'active_setups'
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