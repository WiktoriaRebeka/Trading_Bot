# Lokalizacja: bot_service/pnl_logger_real.py
import logging
from typing import Dict, Any
from datetime import datetime, timezone
from google.cloud import bigquery

from bot_service.bigquery_logger import get_bigquery_client
from shared_lib import constants

logger = logging.getLogger(__name__)

# Definicja referencji do tabeli jest pobierana z centralnego miejsca
REAL_TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_REAL_TRADES_TABLE_ID}"

# ROZSZERZONY SCHEMAT - dodajemy nowe, przydatne pola
REAL_TRADES_HISTORY_SCHEMA = [
    bigquery.SchemaField("alert_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("order_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("direction", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("qty", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("leverage", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("avg_entry_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("avg_exit_price", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("closed_pnl", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("commission", "FLOAT", mode="NULLABLE"),
    bigquery.SchemaField("exit_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED"),
]

def log_real_trade_result(enriched_pnl_data: Dict[str, Any]):
    """Transformuje wzbogacone dane PnL z Bybit i zapisuje je do BigQuery."""
    
    try:
        # Transformujemy dane, korzystając z pełnego rekordu PnL z Bybit
        transformed_data = {
            "alert_id": enriched_pnl_data.get("alert_id", "unknown"),
            "order_id": enriched_pnl_data.get("orderId", "unknown"),
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": "LONG" if enriched_pnl_data.get("side") == "Buy" else "SHORT",
            "qty": float(enriched_pnl_data.get("qty", 0.0)),
            "leverage": int(float(enriched_pnl_data.get("leverage", 1))),
            "avg_entry_price": float(enriched_pnl_data.get("avgEntryPrice", 0.0)),
            "avg_exit_price": float(enriched_pnl_data.get("avgExitPrice", 0.0)),
            "closed_pnl": float(enriched_pnl_data.get("closedPnl", 0.0)),
            "commission": float(enriched_pnl_data.get("cumCommission", 0.0)),
            "exit_type": enriched_pnl_data.get("exitType"), # Np. 'TakeProfit', 'StopLoss', 'CloseBy'
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Błąd podczas transformacji danych PnL: {e}", exc_info=True, extra={"json_fields": {"pnl_data": enriched_pnl_data}})
        return

    logger.info(f"Logowanie realnego wyniku dla {transformed_data['symbol']} (Alert ID: {transformed_data['alert_id']}) do BigQuery.")
    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if errors:
            logger.error(f"Błąd podczas wstawiania realnych wyników do BigQuery: {errors}")
        else:
            logger.info("Pomyślnie zapisano realny wynik transakcji.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu realnych wyników: {e}", exc_info=True)