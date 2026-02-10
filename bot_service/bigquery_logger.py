# Lokalizacja: bot_service/bigquery_logger.py
import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError
from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
ANALYTICAL_TABLE_REF: Optional[bigquery.TableReference] = None
REAL_TRADES_TABLE_REF: Optional[bigquery.TableReference] = None

def initialize_bigquery() -> bool:
    global bigquery_client, ANALYTICAL_TABLE_REF, REAL_TRADES_TABLE_REF
    if bigquery_client is not None:
        logger.info("[BQ_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True
    
    try:
        logger.info("[BQ_INIT] Próba inicjalizacji klienta BigQuery...")
        
        DATASET_LOCATION = "US"
        client = bigquery.Client(location=DATASET_LOCATION)
        
        dataset_ref = client.dataset(constants.BIGQUERY_DATASET_ID)
        
        real_trades_table_id = constants.BIGQUERY_REAL_TRADES_TABLE_ID
        REAL_TRADES_TABLE_REF = dataset_ref.table(real_trades_table_id)
        client.get_table(REAL_TRADES_TABLE_REF)
        logger.info(f"[BQ_INIT] Pomyślnie zweryfikowano tabelę transakcji rzeczywistych: {real_trades_table_id}")
        
        bigquery_client = client
        logger.info(f"[BQ_INIT] Klient BigQuery pomyślnie zainicjalizowany. Lokalizacja: {DATASET_LOCATION}")
        return True
    
    except Exception as e:
        logger.critical(f"[BQ_INIT] KRYTYCZNY BŁĄD: Inicjalizacja klienta BigQuery nie powiodła się: {e}", exc_info=True)
        bigquery_client, ANALYTICAL_TABLE_REF, REAL_TRADES_TABLE_REF = None, None, None
        return False

def get_bigquery_client() -> bigquery.Client:
    if bigquery_client is None:
        logger.error("[BQ_CLIENT] Próba użycia niezainicjalizowanego klienta BigQuery.")
        raise RuntimeError("Klient BigQuery nie został pomyślnie zainicjalizowany.")
    return bigquery_client

def log_analysis_result(result_data: Dict[str, Any]):
    """
    Zapisuje wynik analizy sygnału do BigQuery.
    
    ZMIANA: Używa 'event_id' zamiast 'alert_id' (zgodnie ze schematem BigQuery).
    """
    # POPRAWKA: alert_id → event_id
    event_id = result_data.get('event_id', 'unknown')
    logger.info(f"[BQ_LOGGER][{event_id}] Rozpoczynam proces zapisu wyniku do BigQuery.")
    
    try:
        client = get_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[BQ_LOGGER][{event_id}] Nie można zalogować wyniku: {e}")
        return
    
    try:
        rows_to_insert = [result_data]
        errors = client.insert_rows_json(ANALYTICAL_TABLE_REF, rows_to_insert)
        
        if not errors:
            logger.info(f"[BQ_LOGGER][{event_id}] SUKCES! Rekord zapisany w BigQuery.")
        else:
            logger.error(f"[BQ_LOGGER][{event_id}] Błąd podczas wstawiania wierszy: {errors}")
    
    except GoogleAPICallError as e:
        # POPRAWKA: event_id zamiast alert_id
        logger.error(f"[BQ_LOGGER][{event_id}] Błąd API BigQuery podczas zapisu: {e}", exc_info=True)
    except Exception as e:
        # POPRAWKA: event_id zamiast alert_id
        logger.error(f"[BQ_LOGGER][{event_id}] Krytyczny błąd podczas zapisu: {e}", exc_info=True)