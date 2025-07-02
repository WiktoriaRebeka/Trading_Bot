# trading_bot/bot_service/bigquery_logger.py

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
TABLE_REF: Optional[str] = None

EXPECTED_SCHEMA = {
    "trade_id": str, "timestamp_entry": str, "timestamp_close": str,
    "symbol": str, "direction": str, "main_result": str, "ob_type": str,
    "rr_achieved": float, "rr_1_0_achieved": bool, "rr_1_5_achieved": bool,
    "rr_2_0_achieved": bool, "rr_3_0_achieved": bool, "rr_4_0_achieved": bool,
    "rr_5_0_achieved": bool,
}

# --- NOWA FUNKCJA INICJALIZACYJNA ---
def initialize_bigquery():
    """
    Inicjalizuje globalnego klienta BigQuery. Powinna być wywołana raz 
    na starcie aplikacji wewnątrz fabryki aplikacji (np. w create_app).
    """
    global bigquery_client, TABLE_REF

    # Sprawdzamy, czy klient nie został już zainicjalizowany, aby uniknąć powtórnej pracy.
    if bigquery_client is not None:
        logger.debug("Klient BigQuery jest już zainicjalizowany.")
        return

    try:
        # Tworzymy klienta
        client = bigquery.Client()
        table_ref_str = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
        
        # Proste, ale skuteczne sprawdzenie połączenia i uprawnień przez próbę pobrania metadanych tabeli.
        # Jeśli tabela nie istnieje lub brakuje uprawnień, to polecenie zgłosi wyjątek.
        client.get_table(table_ref_str)
        
        # Jeśli wszystko się udało, przypisujemy klienta i referencję do zmiennych globalnych.
        bigquery_client = client
        TABLE_REF = table_ref_str
        logger.info(f"Klient BigQuery pomyślnie zainicjalizowany. Tabela docelowa: {TABLE_REF}")

    except Exception as e:
        # W przypadku błędu logujemy krytyczną informację i pozostawiamy zmienne jako None.
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)
        bigquery_client, TABLE_REF = None, None


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
    # Sprawdzanie, czy klient został pomyślnie zainicjalizowany.
    if not bigquery_client or not TABLE_REF:
        logger.error("[BQ_LOGGER] Klient BigQuery nie jest zainicjalizowany! Pomijam zapis.")
        return
    
    logger.info(f"[BQ_LOGGER] Przygotowuję do zapisu w BigQuery: {trade_data}")
    sanitized_data = _validate_and_sanitize_data(trade_data)
    if not sanitized_data:
        logger.error(f"[BQ_LOGGER] Dane transakcji nie przeszły walidacji. ID: {trade_data.get('trade_id')}. Pomijam zapis.")
        return
        
    try:
        # get_table nie jest już potrzebne, bo weryfikacja była przy inicjalizacji
        rows_to_insert = [sanitized_data]
        errors = bigquery_client.insert_rows_json(TABLE_REF, rows_to_insert)
        if not errors:
            logger.info(f"[BQ_LOGGER] SUKCES! Pomyślnie wstawiono wiersz dla transakcji ID: {sanitized_data.get('trade_id')}")
        else:
            logger.error(f"[BQ_LOGGER] Błąd podczas wstawiania wierszy do BigQuery dla ID: {sanitized_data.get('trade_id')}. Błędy: {errors}")
    except Exception as e:
        logger.error(f"[BQ_LOGGER] Krytyczny błąd podczas zapisu do BigQuery dla ID: {sanitized_data.get('trade_id')}: {e}", exc_info=True)


def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]) -> bool:
    """
    Bezpiecznie aktualizuje istniejący wiersz w BigQuery danymi z analizy post-mortem
    używając zapytań sparametryzowanych, aby zapobiec SQL Injection.
    """
    # Sprawdzanie, czy klient został pomyślnie zainicjalizowany.
    if not bigquery_client or not TABLE_REF or not updates:
        logger.error("[BQ_UPDATER] Klient BigQuery nie jest zainicjalizowany lub brak danych do aktualizacji. Pomijam zapis.")
        return False

    set_clauses = [f"`{key}` = @{key}" for key in updates.keys()]
    query = f"UPDATE `{TABLE_REF}` SET {', '.join(set_clauses)} WHERE trade_id = @trade_id"

    query_params = [
        bigquery.ScalarQueryParameter("trade_id", "STRING", trade_id)
    ]
    for key, value in updates.items():
        if isinstance(value, bool):
            param_type = "BOOL"
        elif isinstance(value, (float, int)):
            param_type = "FLOAT64"
        else:
            param_type = "STRING"
        query_params.append(bigquery.ScalarQueryParameter(key, param_type, value))

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)

    logger.info(f"[BQ_UPDATER] Wykonuję sparametryzowane zapytanie dla {trade_id}")
    try:
        query_job = bigquery_client.query(query, job_config=job_config)
        query_job.result()

        if query_job.num_dml_affected_rows > 0:
            logger.info(f"[BQ_UPDATER] SUKCES. Pomyślnie zaktualizowano wiersz dla {trade_id}.")
            return True
        else:
            logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla {trade_id} (możliwe, że jest w buforze strumieniowym lub został już usunięty).")
            return False

    except GoogleAPICallError as e:
        if "streaming buffer" in str(e):
            logger.warning(f"[BQ_UPDATER] Oczekiwany błąd bufora strumieniowego dla {trade_id}. Spróbujemy ponownie w kolejnym cyklu.")
        else:
            logger.error(f"[BQ_UPDATER] Błąd API podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"[BQ_UPDATER] Nieoczekiwany błąd podczas aktualizacji dla {trade_id}: {e}", exc_info=True)
        return False
