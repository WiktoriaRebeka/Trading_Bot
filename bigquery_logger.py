# /trading_bot/bigquery_logger.py

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

# Nie potrzebujemy już importu state_manager i time
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
    """
    Sprawdza, czy dane zawierają wszystkie oczekiwane pola i czy mają poprawne typy.
    Zwraca oczyszczony słownik lub None w przypadku błędu.
    """
    sanitized = {}
    missing_keys = []
    
    for key, expected_type in EXPECTED_SCHEMA.items():
        if key not in data or data[key] is None:
            # Dla flag boolean ustawiamy domyślną wartość False
            if expected_type is bool:
                sanitized[key] = False
            else:
                missing_keys.append(key)
        else:
            value = data[key]
            # Prosta konwersja typów, aby zapewnić zgodność
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

#
# --- FUNKCJA update_analyzed_trade_in_bigquery ZOSTAŁA USUNIĘTA, BO NIE JEST JUŻ POTRZEBNA ---
#