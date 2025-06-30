import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

logger = logging.getLogger(__name__)

# Schemat pozostaje taki sam
EXPECTED_SCHEMA = {
    "trade_id": str, "timestamp_entry": str, "timestamp_close": str,
    "symbol": str, "direction": str, "main_result": str, "ob_type": str,
    "rr_achieved": float, "rr_1_0_achieved": bool, "rr_1_5_achieved": bool,
    "rr_2_0_achieved": bool, "rr_3_0_achieved": bool, "rr_4_0_achieved": bool,
    "rr_5_0_achieved": bool,
}

# Inicjalizacja klienta pozostaje taka sama
try:
    bigquery_client = bigquery.Client()
    TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
    logger.info(f"Klient BigQuery pomyślnie zainicjalizowany. Tabela docelowa: {TABLE_REF}")
except Exception as e:
    bigquery_client, TABLE_REF = None, None
    logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)


def _validate_and_sanitize_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Ta funkcja jest poprawna i pozostaje bez zmian
    sanitized = {}
    missing_keys = []
    for key, expected_type in EXPECTED_SCHEMA.items():
        if key not in data or data[key] is None:
            if expected_type is bool:
                sanitized[key] = False
            else:
                missing_keys.append(key)
        else:
            value = data[key]
            try:
                if expected_type is float and not isinstance(value, float):
                    sanitized[key] = float(value)
                elif expected_type is bool and not isinstance(value, bool):
                    sanitized[key] = bool(value)
                else:
                    sanitized[key] = value
            except (ValueError, TypeError):
                logger.error(f"[BQ_VALIDATOR] Nie można przekonwertować wartości dla klucza '{key}' na typ {expected_type}.")
                return None
    if missing_keys:
        logger.error(f"[BQ_VALIDATOR] Brakujące kluczowe pola w danych: {missing_keys}. Pomijam zapis.")
        return None
    return sanitized


def log_trade_to_bigquery(trade_data: Dict):
    """Loguje kompletną transakcję do tabeli historii w BigQuery."""
    if not bigquery_client:
        logger.error("[BQ_LOGGER] Klient BigQuery nie jest zainicjalizowany! Pomijam zapis.")
        return
    logger.info(f"[BQ_LOGGER] Przygotowuję do zapisu w BigQuery: {trade_data}")
    sanitized_data = _validate_and_sanitize_data(trade_data)
    if not sanitized_data:
        logger.error(f"[BQ_LOGGER] Dane transakcji nie przeszły walidacji. ID: {trade_data.get('trade_id')}. Pomijam zapis.")
        return
    try:
        table = bigquery_client.get_table(TABLE_REF)
        rows_to_insert = [sanitized_data]
        errors = bigquery_client.insert_rows_json(table, rows_to_insert)
        if not errors:
            logger.info(f"[BQ_LOGGER] SUKCES! Pomyślnie wstawiono wiersz dla transakcji ID: {sanitized_data.get('trade_id')}")
        else:
            logger.error(f"[BQ_LOGGER] Błąd podczas wstawiania wierszy do BigQuery dla ID: {sanitized_data.get('trade_id')}. Błędy: {errors}")
    except Exception as e:
        logger.error(f"[BQ_LOGGER] Krytyczny błąd podczas zapisu do BigQuery dla ID: {sanitized_data.get('trade_id')}: {e}", exc_info=True)


# --- PRZYWRACAMY KLUCZOWĄ FUNKCJĘ ---
def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]):
    """Aktualizuje istniejący wiersz w BigQuery danymi z analizy post-mortem."""
    if not bigquery_client or not updates: return

    set_clauses = []
    for key, value in updates.items():
        if isinstance(value, str):
            set_clauses.append(f"`{key}` = '{value}'")
        elif isinstance(value, bool):
             set_clauses.append(f"`{key}` = {str(value).upper()}")
        else:
            set_clauses.append(f"`{key}` = {value}")
    
    query = f"UPDATE `{TABLE_REF}` SET {', '.join(set_clauses)} WHERE trade_id = '{trade_id}'"
    
    logger.info(f"[BQ_UPDATER] Wykonuję zapytanie: {query}")
    try:
        query_job = bigquery_client.query(query)
        query_job.result()
        
        if query_job.num_dml_affected_rows > 0:
            logger.info(f"[BQ_UPDATER] Pomyślnie zaktualizowano wiersz dla {trade_id}.")
            
            # --- Naprawiamy import cykliczny ---
            import state_manager
            state_manager.update_analyzed_trade_timestamp(trade_id)
            
        else:
            logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla {trade_id} (prawdopodobnie wciąż w buforze).")

    except GoogleAPICallError as e:
        # Bezpieczniejsze sprawdzanie błędu
        if "streaming buffer" in str(e):
            logger.warning(f"[BQ_UPDATER] Oczekiwany błąd bufora strumieniowego dla {trade_id}. Spróbujemy ponownie później.")
        else:
            logger.error(f"[BQ_UPDATER] Błąd API podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[BQ_UPDATER] Nieoczekiwany błąd podczas aktualizacji dla {trade_id}: {e}", exc_info=True)