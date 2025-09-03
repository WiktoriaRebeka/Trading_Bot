# Lokalizacja: bot_service/pnl_logger.py

import logging
from typing import Optional
from datetime import datetime, timezone
from google.cloud import bigquery
from shared_lib.models import AnalyticalScenario

logger = logging.getLogger(__name__)

# Zmienne globalne dla klienta i referencji do tabeli
bigquery_client: Optional[bigquery.Client] = None
ANALYSIS_TABLE_REF: Optional[str] = None

def initialize_bigquery_for_analysis() -> bool:
    """Inicjalizuje klienta BigQuery dla tabeli wyników analizy."""
    global bigquery_client, ANALYSIS_TABLE_REF
    if bigquery_client:
        logger.info("[BQ_ANALYSIS_INIT] Klient BigQuery dla analizy jest już zainicjalizowany.")
        return True
    try:
        logger.info("[BQ_ANALYSIS_INIT] Inicjalizacja klienta BigQuery dla tabeli 'analysis_results'...")
        # Upewnij się, że te stałe są zdefiniowane w constants.py lub bezpośrednio tutaj
        project_id = "trading-bot-463318"
        dataset_id = "trading_analytics"
        table_id = "analysis_results" # Dedykowana tabela dla wyników analizy
        
        client = bigquery.Client(project=project_id)
        table_ref_str = f"{project_id}.{dataset_id}.{table_id}"
        client.get_table(table_ref_str)  # Walidacja, czy tabela istnieje
        
        bigquery_client = client
        ANALYSIS_TABLE_REF = table_ref_str
        logger.info(f"[BQ_ANALYSIS_INIT] Klient BigQuery dla analizy pomyślnie zainicjalizowany. Tabela: {ANALYSIS_TABLE_REF}")
        return True
    except Exception as e:
        logger.critical(f"[BQ_ANALYSIS_INIT] KRYTYCZNY BŁĄD: Inicjalizacja klienta BigQuery dla analizy nie powiodła się: {e}", exc_info=True)
        bigquery_client, ANALYSIS_TABLE_REF = None, None
        return False

def log_analysis_to_bigquery(scenario: AnalyticalScenario, rr_level: str, result: str):
    """Loguje wynik pojedynczego scenariusza analitycznego do BigQuery."""
    if not bigquery_client or not ANALYSIS_TABLE_REF:
        logger.error(f"[BQ_ANALYSIS_LOGGER] Klient BigQuery nie jest zainicjalizowany. Nie można zapisać wyniku dla {scenario.symbol}.")
        return

    tp_prices = {
        '1.0': scenario.tp_1_0, '1.5': scenario.tp_1_5, '2.0': scenario.tp_2_0,
        '3.0': scenario.tp_3_0, '4.0': scenario.tp_4_0, '5.0': scenario.tp_5_0
    }

    row_to_insert = {
        "alert_id": scenario.alert_id,
        "scenario_id": f"{scenario.alert_id}_{rr_level.replace('.', '_')}",
        "symbol": scenario.symbol,
        "direction": scenario.direction,
        "entry_price": scenario.entry_price,
        "sl_price": scenario.sl_price,
        "tp_price": tp_prices.get(rr_level),
        "rr_level": rr_level,
        "result": result,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        errors = bigquery_client.insert_rows_json(ANALYSIS_TABLE_REF, [row_to_insert])
        if not errors:
            logger.info(f"[{scenario.symbol}] Zapisano wynik analizy do BigQuery: RR {rr_level} -> {result}")
        else:
            logger.error(f"[{scenario.symbol}] Błąd BigQuery podczas zapisu wyniku analizy: {errors}")
    except Exception as e:
        logger.error(f"[{scenario.symbol}] Krytyczny błąd podczas zapisu wyniku analizy do BigQuery: {e}", exc_info=True)