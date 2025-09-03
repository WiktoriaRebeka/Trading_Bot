# Lokalizacja: bot_service/analyzer.py

import logging
from shared_lib.models import Kline, AnalyticalScenario
from bot_service import state_manager
from bot_service.pnl_logger import log_analysis_to_bigquery

logger = logging.getLogger(__name__)

# Zastąp funkcję _check_scenarios
def _check_scenarios(scenario: AnalyticalScenario, kline: Kline) -> (dict, bool):
    # KROK 0: SPRAWDZENIE, CZY WEJŚCIE ZOSTAŁO ZREALIZOWANE
    if scenario.entry_status == 'PENDING':
        entry_triggered = False
        if scenario.direction == 'LONG' and kline.low <= scenario.entry_price:
            entry_triggered = True
        elif scenario.direction == 'SHORT' and kline.high >= scenario.entry_price:
            entry_triggered = True
        
        if entry_triggered:
            logger.info(f"[{scenario.symbol}] ANALIZA: Wejście ({scenario.entry_price}) zostało zrealizowane dla alertu {scenario.alert_id}. Rozpoczynam analizę SL/TP.")
            state_manager.update_analytical_scenario_entry_status(scenario.alert_id, 'TRIGGERED')
            return {}, True # Zwracamy pusty status, aby wymusić odświeżenie
        else:
            return {}, False # Wejście nie zrealizowane, nic więcej nie robimy

    # PONIŻSZY KOD WYKONA SIĘ TYLKO JEŚLI entry_status == 'TRIGGERED'
    new_status = scenario.scenario_status.copy()
    has_changed = False

    # KROK 1: SPRAWDZENIE STOP LOSS
    sl_hit = False
    if scenario.direction == 'LONG' and kline.low <= scenario.sl_price:
        sl_hit = True
    elif scenario.direction == 'SHORT' and kline.high >= scenario.sl_price:
        sl_hit = True

    if sl_hit:
        logger.warning(f"[{scenario.symbol}] ANALIZA: Stop Loss ({scenario.sl_price}) trafiony dla alertu {scenario.alert_id}.")
        for rr_level, status in new_status.items():
            if status == 'ACTIVE':
                new_status[rr_level] = 'LOSE'
                log_analysis_to_bigquery(scenario, rr_level, 'LOSE')
                has_changed = True
        return new_status, has_changed

    # KROK 2: SPRAWDZENIE TAKE PROFIT
    tp_levels = {
        '1.0': scenario.tp_1_0, '1.5': scenario.tp_1_5, '2.0': scenario.tp_2_0,
        '3.0': scenario.tp_3_0, '4.0': scenario.tp_4_0, '5.0': scenario.tp_5_0
    }
    for rr_level, tp_price in tp_levels.items():
        if new_status.get(rr_level) == 'ACTIVE':
            tp_hit = False
            if scenario.direction == 'LONG' and kline.high >= tp_price:
                tp_hit = True
            elif scenario.direction == 'SHORT' and kline.low <= tp_price:
                tp_hit = True
            
            if tp_hit:
                logger.info(f"[{scenario.symbol}] ANALIZA: Scenariusz {rr_level}R osiągnął TP ({tp_price}) dla alertu {scenario.alert_id}.")
                new_status[rr_level] = 'WIN'
                log_analysis_to_bigquery(scenario, rr_level, 'WIN')
                has_changed = True
    return new_status, has_changed

def run_analysis_cycle():
    """Główna pętla cyklu analitycznego."""
    logger.info("--- ROZPOCZYNAM PĘTLĘ ANALIZY SCENARIUSZY ---")
    
    active_scenarios_docs = list(state_manager.get_all_active_scenarios())
    if not active_scenarios_docs:
        logger.info("Brak aktywnych scenariuszy do analizy.")
        return

    symbols_to_check = {doc.to_dict()['symbol'] for doc in active_scenarios_docs}
    klines_data = state_manager.get_latest_klines_from_cache(list(symbols_to_check))

    for scenario_doc in active_scenarios_docs:
        scenario = None
        try:
            scenario_data = scenario_doc.to_dict()
            scenario = AnalyticalScenario.model_validate(scenario_data)
            
            kline_dict = klines_data.get(scenario.symbol)
            if not kline_dict:
                logger.warning(f"[{scenario.symbol}] Brak danych kline w cache dla aktywnego scenariusza {scenario.alert_id}. Pomijam w tym cyklu.")
                continue
            
            kline = Kline.model_validate(kline_dict)
            
            new_status, has_changed = _check_scenarios(scenario, kline)

            if has_changed:
                state_manager.update_analytical_scenario_status(scenario.alert_id, new_status)
                
                if all(status != 'ACTIVE' for status in new_status.values()):
                    logger.info(f"[{scenario.symbol}] Wszystkie scenariusze dla alertu {scenario.alert_id} zostały zakończone. Rozpoczynam czyszczenie.")
                    state_manager.delete_analytical_scenario(scenario.alert_id)

        except Exception as e:
            alert_id = scenario.alert_id if scenario else scenario_doc.id
            logger.error(f"[ANALIZA][{alert_id}] Krytyczny błąd podczas analizy: {e}", exc_info=True)