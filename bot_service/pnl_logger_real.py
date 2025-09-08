# Lokalizacja: bot_service/pnl_logger_real.py
import logging
from typing import Dict, Any
from google.cloud import bigquery

from bot_service.bigquery_logger import initialize_bigquery, get_bigquery_client
from shared_lib import constants

logger = logging.getLogger(__name__)

# Definicja nowej tabeli
REAL_TRADES_TABLE_ID = "real_trades_history"
REAL_TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{REAL_TRADES_TABLE_ID}"

# Schemat jest identyczny jak dla backtestu, aby umożliwić porównania
REAL_TRADES_HISTORY_SCHEMA = [
    bigquery.SchemaField("analysis_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("direction", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("entry_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("sl_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("target_level", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("target_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("result", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_alert", "TIMESTAMP", mode="NULLABLE"), # Może być niedostępny
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("risk_percentage", "FLOAT", mode="NULLABLE"),
]

def log_real_trade_result(pnl_data: Dict[str, Any]):
    """Transformuje dane PnL z Bybit i zapisuje je do BigQuery."""
    
    # Transformacja danych z formatu Bybit na nasz schemat
    # To jest uproszczona transformacja, która wymaga dopracowania
    try:
        is_win = float(pnl_data.get("closedPnl", 0.0)) > 0
        
        # W realnym handlu mamy jedno wejście i jedno wyjście.
        # Symulujemy to jako jeden scenariusz.
        transformed_data = {
            "analysis_id": pnl_data.get("orderId", "unknown"),
            "symbol": pnl_data.get("symbol"),
            "direction": "LONG" if pnl_data.get("side") == "Buy" else "SHORT",
            "entry_price": float(pnl_data.get("avgEntryPrice", 0.0)),
            "sl_price": float(pnl_data.get("stopLoss", 0.0)), # Uwaga: to pole może być niedostępne
            "target_level": "CLOSED", # Używamy specjalnej etykiety
            "target_price": float(pnl_data.get("avgExitPrice", 0.0)),
            "result": "WIN" if is_win else "LOSE",
            "timestamp_alert": None, # Tego nie mamy w danych PnL
            "timestamp_entry": datetime.fromtimestamp(int(pnl_data.get("createdTime")) / 1000).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(pnl_data.get("updatedTime")) / 1000).isoformat(),
            "risk_percentage": None # Tego nie mamy w danych PnL, musielibyśmy to przechowywać
        }
    except Exception as e:
        logger.error(f"Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return

    logger.info(f"Logowanie realnego wyniku dla {transformed_data['symbol']} do BigQuery.")
    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if errors:
            logger.error(f"Błąd podczas wstawiania realnych wyników do BigQuery: {errors}")
        else:
            logger.info("Pomyślnie zapisano realny wynik transakcji.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu realnych wyników: {e}", exc_info=True)