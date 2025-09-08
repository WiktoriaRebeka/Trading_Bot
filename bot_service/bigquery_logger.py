# Lokalizacja: bot_service/bigquery_logger.py

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
TABLE_REF: Optional[str] = None

# Zgodnie z nową specyfikacją
NEW_TRADES_HISTORY_SCHEMA = [
    bigquery.SchemaField("analysis_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("direction", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("entry_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("sl_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("target_level", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("target_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("result", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_alert", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("risk_percentage", "FLOAT", mode="NULLABLE", description="Procentowa odległość od ceny wejścia do SL, obliczona przy walidacji alertu."),
]

def initialize_bigquery() -> bool:
    global bigquery_client, TABLE_REF
    if bigquery_client is not None:
        logger.info("[BQ_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True
    try:
        logger.info("[BQ_INIT] Próba inicjalizacji klienta BigQuery...")
        client = bigquery.Client()
        # === KLUCZOWA POPRAWKA ===
        # Używamy nowej, poprawnej nazwy stałej z pliku constants.py
        table_ref_str = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_ANALYTICAL_TABLE_ID}"
        client.get_table(table_ref_str) # Sprawdzenie, czy tabela istnieje
        bigquery_client = client
        TABLE_REF = table_ref_str
        logger.info(f"[BQ_INIT] Klient BigQuery pomyślnie zainicjalizowany. Tabela: {TABLE_REF}")
        return True
    except Exception as e:
        logger.critical(f"[BQ_INIT] KRYTYCZNY BŁĄD: Inicjalizacja klienta BigQuery nie powiodła się: {e}", exc_info=True)
        bigquery_client, TABLE_REF = None, None
        return False

def get_bigquery_client() -> bigquery.Client:
    if bigquery_client is None:
        logger.error("[BQ_CLIENT] Próba użycia niezainicjalizowanego klienta BigQuery.")
        raise RuntimeError("Klient BigQuery nie został pomyślnie zainicjalizowany.")
    return bigquery_client

def log_analysis_result(result_data: Dict[str, Any]):
    """
    Zapisuje pojedynczy, atomowy wynik rozstrzygnięcia scenariusza do BigQuery.
    """
    analysis_id = result_data.get('analysis_id')
    logger.info(f"[BQ_LOGGER][{analysis_id}] Rozpoczynam proces zapisu wyniku do BigQuery.")
    try:
        client = get_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[BQ_LOGGER][{analysis_id}] Nie można zalogować wyniku: {e}")
        return

    try:
        rows_to_insert = [result_data]
        errors = client.insert_rows_json(TABLE_REF, rows_to_insert)
        if not errors:
            logger.info(f"[BQ_LOGGER][{analysis_id}] SUKCES! Pomyślnie wstawiono wiersz dla targetu {result_data.get('target_level')}.")
        else:
            logger.error(f"[BQ_LOGGER][{analysis_id}] Błąd podczas wstawiania wierszy: {errors}")
    except GoogleAPICallError as e:
        logger.error(f"[BQ_LOGGER][{analysis_id}] Błąd API BigQuery podczas zapisu: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[BQ_LOGGER][{analysis_id}] Krytyczny błąd podczas zapisu: {e}", exc_info=True)