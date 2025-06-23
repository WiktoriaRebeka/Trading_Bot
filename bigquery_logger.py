# /trading_bot/bigquery_logger.py (WERSJA Z DEBUGOWANIEM)

import logging
from typing import Dict
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

logger = logging.getLogger(__name__)

try:
    bigquery_client = bigquery.Client()
    TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
    logger.info(f"Klient BigQuery zainicjalizowany pomyślnie. Tabela: {TABLE_REF}")
except Exception as e:
    bigquery_client = None
    TABLE_REF = None
    logger.critical(f"Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)


def log_trade_to_bigquery(trade_data: Dict):
    """
    Wstawia pojedynczy, przygotowany wiersz (słownik) do tabeli historii transakcji w BigQuery.
    """
    if not bigquery_client or not TABLE_REF:
        logger.error("[BQ_LOGGER] Klient BigQuery nie jest dostępny. Pomijam logowanie transakcji.")
        return

    # --- DODANA LINIA DO DEBUGOWANIA ---
    logger.info(f"[BQ_LOGGER] Próba zapisu danych do BigQuery: {trade_data}")
    # ------------------------------------

    try:
        errors = bigquery_client.insert_rows_json(TABLE_REF, [trade_data])
        if not errors:
            logger.info(f"[BQ_LOGGER] Pomyślnie zapisano transakcję {trade_data.get('trade_id')} do BigQuery.")
        else:
            logger.error(f"[BQ_LOGGER] Wystąpiły błędy podczas wstawiania danych do BigQuery dla {trade_data.get('trade_id')}: {errors}")
            
    except GoogleAPICallError as e:
        logger.error(f"[BQ_LOGGER] Błąd API podczas zapisu do BigQuery dla {trade_data.get('trade_id')}: {e}", exc_info=True)