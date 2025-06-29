# /trading_bot/bigquery_logger.py (WERSJA FINALNA)

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants
# Import state_managera jest potrzebny do aktualizacji timestampu
import state_manager
import time 

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
except Exception as e:
    bigquery_client, TABLE_REF = None, None
    logger.critical(f"Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)

def _validate_and_sanitize_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # ... (kod bez zmian)
    pass

def log_trade_to_bigquery(trade_data: Dict):
    # ... (kod bez zmian)
    pass

def update_analyzed_trade_in_bigquery(trade_id: str, updates: Dict[str, Any]):
    """Aktualizuje istniejący wiersz w BigQuery z mechanizmem ponawiania prób."""
    if not bigquery_client or not updates:
        return

    set_clauses = []
    for key, value in updates.items():
        if isinstance(value, str):
            set_clauses.append(f"`{key}` = '{value}'")
        elif isinstance(value, bool):
            set_clauses.append(f"`{key}` = {str(value).upper()}")
        else:
            set_clauses.append(f"`{key}` = {value}")
    
    query = f"UPDATE `{TABLE_REF}` SET {', '.join(set_clauses)} WHERE trade_id = '{trade_id}'"
    
    logger.info(f"[BQ_UPDATER] Przygotowuję zapytanie: {query}")

    # --- POCZĄTEK ZMIAN: Mechanizm ponawiania ---
    max_retries = 3
    for attempt in range(max_retries):
        try:
            query_job = bigquery_client.query(query)
            query_job.result()  # Czekaj na zakończenie zadania
            
            if query_job.num_dml_affected_rows > 0:
                logger.info(f"[BQ_UPDATER] Pomyślnie zaktualizowano wiersz dla {trade_id}.")
                state_manager.update_analyzed_trade_timestamp(trade_id)
                return  # Zakończ funkcję po sukcesie
            else:
                logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla {trade_id}. To może być normalne, jeśli wiersz jeszcze nie jest widoczny.")
                return # Zakończ, nie ma sensu próbować ponownie

        except GoogleAPICallError as e:
            # Sprawdzamy, czy to nasz oczekiwany błąd bufora
            if "streaming buffer" in str(e).lower():
                logger.warning(f"[BQ_UPDATER] Oczekiwany błąd bufora dla {trade_id}. Próba {attempt + 1}/{max_retries}. Ponawiam za chwilę...")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Czekaj 1, 2, 4 sekundy (exponential backoff)
                else:
                    logger.error(f"[BQ_UPDATER] Nie udało się zaktualizować {trade_id} po {max_retries} próbach z powodu błędu bufora.")
            else:
                # To inny, nieoczekiwany błąd API
                logger.error(f"[BQ_UPDATER] Błąd API podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)
                return # Przerwij po nieoczekiwanym błędzie
        except Exception as e:
            logger.error(f"[BQ_UPDATER] Nieoczekiwany błąd podczas aktualizacji dla {trade_id}: {e}", exc_info=True)
            return # Przerwij po nieoczekiwanym błędzie
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
            # Po udanej aktualizacji BQ, aktualizujemy timestamp w Firestore
            state_manager.update_analyzed_trade_timestamp(trade_id)
        else:
            logger.warning(f"[BQ_UPDATER] Nie znaleziono wiersza do aktualizacji dla {trade_id} (prawdopodobnie wciąż w buforze).")

    except GoogleAPICallError as e:
        if "streaming buffer" in e.message:
            logger.warning(f"[BQ_UPDATER] Oczekiwany błąd bufora strumieniowego dla {trade_id}. Spróbujemy ponownie później.")
        else:
            logger.error(f"[BQ_UPDATER] Błąd API podczas aktualizacji wiersza dla {trade_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[BQ_UPDATER] Nieoczekiwany błąd podczas aktualizacji dla {trade_id}: {e}", exc_info=True)