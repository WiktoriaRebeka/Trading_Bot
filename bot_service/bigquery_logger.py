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

        analytical_table_id = constants.BIGQUERY_REAL_TRADES_TABLE_ID
        ANALYTICAL_TABLE_REF = dataset_ref.table(analytical_table_id)
        logger.info(f"[BQ_INIT] ANALYTICAL_TABLE_REF ustawiony na: {analytical_table_id}")

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
    Loguje dane analityczne sygnału do Cloud Logging.
    BigQuery insert wyłączony do czasu utworzenia tabeli market_structure_signals.
    """
    event_id = result_data.get('event_id', 'unknown')
    logger.info(
        f"[SIGNAL_ANALYTICS][{event_id}] "
        f"symbol={result_data.get('symbol')} "
        f"direction={result_data.get('direction')} "
        f"entry={result_data.get('entry')} "
        f"sl={result_data.get('sl')} "
        f"tp={result_data.get('tp')} "
        f"score={(result_data.get('raw_context') or {}).get('confidence_score', 0):.1f}"
    )