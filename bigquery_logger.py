# /trading_bot/bigquery_logger.py (WERSJA FINALNA v6.4)

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

logger = logging.getLogger(__name__)

EXPECTED_SCHEMA = {
    "trade_id": str, "timestamp_entry": str, "timestamp_close": str,
    "symbol": str, "direction": str, "main_result": str, "ob_type": str,
    "rr_achieved": float, "rr_1_0_achieved": bool, "rr_1_5_achieved": bool,
    "rr_2_0_achieved": bool, "rr_3_0_achieved": bool, "rr_4_0_achieved": bool,
    "rr_5_0_achieved": bool,
}

try:
    bigquery_client = bigquery.Client()
    TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
    logger.info(f"Klient BigQuery zainicjalizowany pomyślnie. Tabela: {TABLE_REF}")
except Exception as e:
    bigquery_client, TABLE_REF = None, None
    logger.critical(f"Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)

def _validate_and_sanitize_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sanitized_data = {}
    for key, expected_type in EXPECTED_SCHEMA.items():
        value = data.get(key)
        if value is None and key in data:
            sanitized_data[key] = None
            continue
        if value is None:
            if expected_type == str: sanitized_data[key] = "N/A"
            elif expected_type == float: sanitized_data[key] = 0.0
            elif expected_type == bool: sanitized_data[key] = False
            else: sanitized_data[key] = None
            continue
        try:
            if not isinstance(value, expected_type):
                sanitized_data[key] = expected_type(value)
            else:
                sanitized_data[key] = value
        except (ValueError, TypeError):
            logger.error(f"[BQ_VALIDATOR] Błąd konwersji typu dla klucza '{key}'.")
            return None
    return sanitized_data

def log_trade_to_bigquery(trade_data: Dict):
    """Wstawia nowy, kompletny wiersz do BigQuery po zamknięciu transakcji."""
    if not bigquery_client: return
    
    sanitized_trade_data = _validate_and_sanitize_data(trade_data)
    if sanitized_trade_data is None:
        logger.error(f"Błąd walidacji danych dla {trade_data.get('trade_id')}. Zapis do BQ przerwany.")
        return

    logger.info(f"[BQ_LOGGER] Zapisuję do BigQuery: {sanitized_trade_data}")
    try:
        errors = bigquery_client.insert_rows_json(TABLE_REF, [sanitized_trade_data])
        if not errors:
            logger.info(f"[BQ_LOGGER] Pomyślnie zapisano transakcję {sanitized_trade_data.get('trade_id')}.")
        else:
            logger.error(f"[BQ_LOGGER] Błędy API podczas wstawiania danych do BQ dla {sanitized_trade_data.get('trade_id')}: {errors}")
    except Exception as e:
        logger.error(f"[BQ_LOGGER] Błąd API podczas zapisu do BQ dla {sanitized_trade_data.get('trade_id')}: {e}", exc_info=True)

def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]):
    """Aktualizuje istniejący wiersz w BigQuery danymi z analizy post-mortem."""
    if not bigquery_client or not updates: return

    set_clauses = []
    for key, value in updates.items():
        if isinstance(value, str):
            set_clauses.append(f"{key} = '{value}'")
        else:
            set_clauses.append(f"{key} = {value}")
    
    query = f"UPDATE `{TABLE_REF}` SET {', '.join(set_clauses)} WHERE trade_id = '{trade_id}'"
    
    logger.info(f"[BQ_UPDATER] Wykonuję zapytanie: {query}")
    try:
        query_job = bigquery_client.query(query)
        query_job.result()
        if query_job.num_dml_affected_rows > 0:
            logger.info(f"[BQ_UPDATER] Pomyślnie zaktualizowano wiersz dla {trade_id}.")
        else:
            logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla {trade_id}.")
    except Exception as e:
        logger.error(f"[BQ_UPDATER] Błąd podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)