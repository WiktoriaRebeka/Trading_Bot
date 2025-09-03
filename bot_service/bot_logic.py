# Lokalizacja: bot_service/bot_logic.py

import logging
from decimal import Decimal
from typing import Any, Dict, List

from bot_service import state_manager, analyzer
from bot_service.bybit_executor import BybitExecutor
from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not newly_fetched_alerts:
        return
    logger.info(f"Otrzymano {len(newly_fetched_alerts)} nowych alertów do analizy.")
    
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'N/A')
        try:
            alert_data = AlertData.model_validate(alert_dict)
            symbol = alert_data.symbol

            # Sprawdzamy, czy już nie analizujemy tego alertu
            if state_manager.get_active_setup(symbol):
                logger.warning(f"[{symbol}] Już istnieje aktywny setup/analiza. Ignoruję nowy alert {alert_id}.")
                continue

            # --- NOWY FILTR BEZPIECZEŃSTWA 0.05% ---
            MIN_SL_DISTANCE_PERCENT = Decimal("0.0005") # 0.05%
            entry_price = Decimal(str(alert_data.entry))
            sl_price = Decimal(str(alert_data.sl))

            if entry_price > 0:
                sl_distance_percentage = abs(entry_price - sl_price) / entry_price
                if sl_distance_percentage < MIN_SL_DISTANCE_PERCENT:
                    logger.warning(
                        f"[{symbol}] Alert odrzucony. Odległość SL ({sl_distance_percentage:.4%}) "
                        f"jest mniejsza niż wymagane minimum ({MIN_SL_DISTANCE_PERCENT:.4%})."
                    )
                    continue # Pomiń ten alert
            # --- KONIEC FILTRA ---

            # Tworzymy "teczkę sprawy" do analizy
            state_manager.create_analytical_scenario(alert_data)
            
            # Tworzymy również pusty setup, aby blokować kolejne alerty na tym symbolu
            state_manager.create_setup_from_alert(alert_data)

        except Exception as e:
            logger.critical(f"[Alert: {alert_id}] Nieoczekiwany błąd w process_new_alerts: {e}", exc_info=True)

def run_trading_logic(bybit_executor: BybitExecutor):
    # Główna pętla logiki teraz tylko uruchamia analizator
    analyzer.run_analysis_cycle()

def sync_pnl_history(bybit_executor: BybitExecutor):
    # Ta funkcja jest teraz wyłączona, ponieważ nie handlujemy
    logger.info("--- Pętla `sync_pnl_history` jest wyłączona (Tryb Analityczny). ---")
    pass