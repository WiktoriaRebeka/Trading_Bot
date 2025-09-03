# Lokalizacja: bot_service/pnl_logger.py

import logging
from typing import Dict, Any, Optional

from google.cloud import bigquery

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
REALIZED_TRADES_TABLE_REF = "trading-bot-463318.trading_analytics.realized_trades_pnl"

def initialize_pnl_logger() -> bool:
    global bigquery_client
    if bigquery_client is not None:
        logger.info("[PNL_LOGGER_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True
    try:
        logger.info("[PNL_LOGGER_INIT] Próba inicjalizacji klienta BigQuery...")
        client = bigquery.Client()
        client.get_table(REALIZED_TRADES_TABLE_REF)
        bigquery_client = client
        logger.info(f"[PNL_LOGGER_INIT] Klient BigQuery pomyślnie zainicjalizowany. Tabela: {REALIZED_TRADES_TABLE_REF}")
        return True
    except Exception as e:
        logger.critical(f"[PNL_LOGGER_INIT] KRYTYCZNY BŁĄD: Inicjalizacja PNL Loggera nie powiodła się: {e}", exc_info=True)
        bigquery_client = None
        return False

def get_pnl_bigquery_client() -> bigquery.Client:
    if bigquery_client is None:
        raise RuntimeError("Klient BigQuery dla PNL Loggera nie został pomyślnie zainicjalizowany.")
    return bigquery_client

def log_realized_trade(trade_pnl_data: Dict[str, Any]):
    trade_id = trade_pnl_data.get('trade_id', 'N/A')
    logger.info(f"[{trade_id}] Otrzymano polecenie zapisu do BigQuery z danymi: {trade_pnl_data}")
    
    try:
        client = get_pnl_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[{trade_id}] BŁĄD KRYTYCZNY: Nie można zapisać do BigQuery, ponieważ klient nie jest zainicjalizowany: {e}")
        return

    required_keys = ['trade_id', 'symbol', 'realized_pnl_usdt', 'final_result']
    if not all(key in trade_pnl_data for key in required_keys):
        logger.error(f"[{trade_id}] BŁĄD: Otrzymano niekompletne dane do zapisu P&L. Pomijam. Dane: {trade_pnl_data}")
        return

    try:
        rows_to_insert = [trade_pnl_data]
        logger.info(f"[{trade_id}] --- ROZPOCZYNAM ZAPIS DO TABELI BIGQUERY ---")
        errors = client.insert_rows_json(REALIZED_TRADES_TABLE_REF, rows_to_insert)
        
        if not errors:
            logger.info(f"[{trade_id}] --- SUKCES! Pomyślnie zapisano dane w BigQuery. ---")
        else:
            logger.error(f"[{trade_id}] BŁĄD: BigQuery zwróciło błędy podczas wstawiania wierszy: {errors}")
    except Exception as e:
        logger.error(f"[{trade_id}] KRYTYCZNY, NIEOCZEKIWANY BŁĄD podczas zapisu do BigQuery: {e}", exc_info=True)