# Lokalizacja: bot_service/bigquery_logger.py

import logging
from typing import Dict, Any, Optional, Set
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
TABLE_REF: Optional[str] = None

EXPECTED_SCHEMA = {
    "trade_id": str, "timestamp_entry": str, "timestamp_close": str, "symbol": str,
    "direction": str, "main_result": str, "ob_type": str, "rr_achieved": float,
    "rr_1_0_achieved": bool, "rr_1_5_achieved": bool, "rr_2_0_achieved": bool,
    "rr_3_0_achieved": bool, "rr_4_0_achieved": bool, "rr_5_0_achieved": bool,
}

UPDATABLE_COLUMNS: Set[str] = {
    "rr_achieved", "rr_1_0_achieved", "rr_1_5_achieved", "rr_2_0_achieved",
    "rr_3_0_achieved", "rr_4_0_achieved", "rr_5_0_achieved", "timestamp_close", "main_result"
}

def initialize_bigquery() -> bool:
    global bigquery_client, TABLE_REF
    if bigquery_client is not None:
        logger.info("[BQ_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True
    try:
        logger.info("[BQ_INIT] Próba inicjalizacji klienta BigQuery...")
        client = bigquery.Client()
        table_ref_str = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
        client.get_table(table_ref_str)
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

def _validate_and_sanitize_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sanitized = {}
    missing_keys = []
    for key, expected_type in EXPECTED_SCHEMA.items():
        if key not in data or data[key] is None:
            if expected_type is bool: sanitized[key] = False
            elif key in ["trade_id", "symbol", "direction", "main_result"]: missing_keys.append(key)
            else: sanitized[key] = None
        else:
            value = data[key]
            try:
                if expected_type is float and not isinstance(value, float): sanitized[key] = float(value)
                elif expected_type is bool and not isinstance(value, bool): sanitized[key] = bool(value)
                else: sanitized[key] = value
            except (ValueError, TypeError):
                logger.error(f"[BQ_VALIDATOR] Nie można przekonwertować wartości dla klucza '{key}'.")
                return None
    if missing_keys:
        logger.error(f"[BQ_VALIDATOR] Brakujące kluczowe pola w danych: {missing_keys}. Pomijam zapis.")
        return None
    return sanitized

def log_trade_to_bigquery(trade_data: Dict):
    logger.info(f"[BQ_LOGGER][{trade_data.get('trade_id')}] Rozpoczynam proces zapisu do BigQuery.")
    try:
        client = get_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[BQ_LOGGER][{trade_data.get('trade_id')}] Nie można zalogować transakcji: {e}")
        return
    
    sanitized_data = _validate_and_sanitize_data(trade_data)
    if not sanitized_data:
        logger.error(f"[BQ_LOGGER][{trade_data.get('trade_id')}] Dane nie przeszły walidacji. Pomijam zapis.")
        return
    try:
        rows_to_insert = [sanitized_data]
        errors = client.insert_rows_json(TABLE_REF, rows_to_insert)
        if not errors:
            logger.info(f"[BQ_LOGGER][{sanitized_data.get('trade_id')}] SUKCES! Pomyślnie wstawiono wiersz.")
        else:
            logger.error(f"[BQ_LOGGER][{sanitized_data.get('trade_id')}] Błąd podczas wstawiania wierszy: {errors}")
    except Exception as e:
        logger.error(f"[BQ_LOGGER][{sanitized_data.get('trade_id')}] Krytyczny błąd podczas zapisu: {e}", exc_info=True)

def _get_bq_type(value: Any) -> str:
    if isinstance(value, bool): return "BOOL"
    if isinstance(value, int): return "INT64"
    if isinstance(value, float): return "FLOAT64"
    return "STRING"

def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]):
    logger.info(f"[BQ_UPDATER][{trade_id}] Rozpoczynam proces aktualizacji w BigQuery z danymi: {updates}")
    try:
        client = get_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[BQ_UPDATER][{trade_id}] Nie można zaktualizować transakcji: {e}")
        return

    valid_updates = {k: v for k, v in updates.items() if k in UPDATABLE_COLUMNS}
    if not valid_updates:
        logger.warning(f"[BQ_UPDATER][{trade_id}] Brak prawidłowych pól do aktualizacji. Pomijam.")
        return

    set_clauses = [f"{key} = @{key}" for key in valid_updates.keys()]
    query = f"UPDATE `{TABLE_REF}` SET {', '.join(set_clauses)} WHERE trade_id = @trade_id"
    
    params = [bigquery.ScalarQueryParameter("trade_id", "STRING", trade_id)]
    params.extend([bigquery.ScalarQueryParameter(key, _get_bq_type(value), value) for key, value in valid_updates.items()])
    job_config = bigquery.QueryJobConfig(query_parameters=params)

    try:
        query_job = client.query(query, job_config=job_config)
        query_job.result()
        logger.info(f"[BQ_UPDATER][{trade_id}] SUKCES! Pomyślnie zaktualizowano transakcję.")
    except GoogleAPICallError as e:
        logger.error(f"[BQ_UPDATER][{trade_id}] Błąd API BigQuery podczas aktualizacji: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[BQ_UPDATER][{trade_id}] Nieoczekiwany błąd podczas aktualizacji: {e}", exc_info=True)