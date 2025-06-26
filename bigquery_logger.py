# /trading_bot/bigquery_logger.py (WERSJA FINALNA - Architektura Wielo-Kolekcyjna)

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

logger = logging.getLogger(__name__)

# SCHEMAT ZGODNY Z FINALNĄ, UPROSZCZONĄ TABELĄ W BIGQUERY
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
    # ... (ta funkcja pozostaje bez zmian)
    pass

def log_trade_to_bigquery(trade_data: Dict):
    """Wstawia nowy, kompletny wiersz do BigQuery po zamknięciu transakcji."""
    # ... (ta funkcja pozostaje bez zmian)
    pass

def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]):
    """
    Aktualizuje istniejący wiersz w BigQuery danymi z analizy post-mortem.
    UWAGA: Ta funkcja wymaga, aby tabela nie miała bufora strumieniowego lub
    aby dane były już w magazynie trwałym.
    """
    if not bigquery_client or not TABLE_REF:
        logger.error("[BQ_UPDATER] Klient BigQuery nie jest dostępny. Pomijam aktualizację.")
        return

    if not updates:
        return

    set_clauses = ", ".join([f"{key} = {repr(value)}" for key, value in updates.items()])
    
    query = f"""
        UPDATE `{TABLE_REF}`
        SET {set_clauses}
        WHERE trade_id = '{trade_id}'
    """
    
    logger.info(f"[BQ_UPDATER] Wykonuję zapytanie aktualizujące dla {trade_id}: {query}")
    try:
        query_job = bigquery_client.query(query)
        query_job.result()  # Czeka na zakończenie zadania
        if query_job.num_dml_affected_rows > 0:
            logger.info(f"[BQ_UPDATER] Pomyślnie zaktualizowano wiersz dla transakcji {trade_id}.")
        else:
            logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla transakcji {trade_id}. Może jeszcze być w buforze.")
    except Exception as e:
        logger.error(f"[BQ_UPDATER] Błąd podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)
