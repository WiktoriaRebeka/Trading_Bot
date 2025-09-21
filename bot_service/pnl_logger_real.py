# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Dict, Any
from datetime import datetime, timezone
from google.cloud import bigquery
from decimal import Decimal, ROUND_DOWN

from bot_service import state_manager 
from bot_service.bigquery_logger import get_bigquery_client, initialize_bigquery
from shared_lib import constants

logger = logging.getLogger(__name__)

REAL_TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_REAL_TRADES_TABLE_ID}"

REAL_TRADES_HISTORY_SCHEMA = [
    bigquery.SchemaField("alert_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("order_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("direction", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("qty", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("leverage", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("avg_entry_price", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("avg_exit_price", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("entry_value_usdt", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("exit_value_usdt", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("gross_pnl_usdt", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("commission_usdt", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("net_pnl_usdt", "NUMERIC", mode="REQUIRED"),
    bigquery.SchemaField("exit_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("sl_price", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("planned_risk_usdt", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("realized_rrr", "NUMERIC", mode="NULLABLE"),
]


def log_closed_positions_pnl(executor: BybitExecutor) -> int:
    """
    Pobiera historię zamkniętych pozycji, dopasowuje je po symbolu z Firestore i loguje do BigQuery.
    """
    logger.info("[PNL_LOGGER] Rozpoczynam cykl logowania PnL.")
    last_check_ts_dt = load_last_processed_timestamp("pnl_logger_last_fetch_state")
    logger.info(f"[PNL_LOGGER] Sprawdzam zamknięte pozycje od: {last_check_ts_dt.isoformat()}")
    
    start_time_ms = int(last_check_ts_dt.timestamp() * 1000)
    pnl_records = executor.get_closed_pnl_history(start_time_ms=start_time_ms)
    
    if not pnl_records:
        logger.info("[PNL_LOGGER] Nie znaleziono nowych zamkniętych pozycji na Bybit od ostatniego sprawdzenia.")
        return 0

    logger.info(f"[PNL_LOGGER] Znaleziono {len(pnl_records)} zamkniętych pozycji na Bybit. Rozpoczynam przetwarzanie.")
    new_max_ts = last_check_ts_dt
    processed_count = 0
    
    for pnl_record in pnl_records:
        symbol_from_bybit = pnl_record.get("symbol")
        if not symbol_from_bybit:
            logger.warning("[PNL_LOGGER] Pominięto rekord PnL bez symbolu.", extra={"json_fields": {"pnl_record": pnl_record}})
            continue

        # --- KLUCZOWA POPRAWKA ---
        # Normalizujemy symbol z Bybit (np. 'BTCUSDT') do naszego formatu w bazie ('BTCUSDT.P')
        # ZANIM przekażemy go do funkcji wyszukującej.
        symbol_to_find = symbol_from_bybit if symbol_from_bybit.endswith('.P') else f"{symbol_from_bybit}.P"
        logger.info(f"[PNL_LOGGER] Próba znalezienia dopasowania dla symbolu '{symbol_from_bybit}' używając klucza '{symbol_to_find}'")
        
        active_order_data = state_manager.get_active_order_by_symbol(symbol_to_find)
        
        if not active_order_data:
            logger.warning(f"[PNL_LOGGER] Nie znaleziono aktywnego zlecenia dla symbolu {symbol_to_find} w Firestore. Prawdopodobnie transakcja manualna. Pomijam.")
            continue

        alert_id = active_order_data.get('alert_id', 'unknown')
        original_order_id = active_order_data.get('orderId')

        if not original_order_id:
            logger.error(f"[PNL_LOGGER] Krytyczny błąd: znaleziono dopasowanie dla {symbol_to_find}, ale brak orderId w dokumencie Firestore. Pomijam.", extra={"json_fields": active_order_data})
            continue

        logger.info(f"[PNL_LOGGER] Pomyślnie dopasowano zamkniętą pozycję {symbol_to_find} do alertu {alert_id} (Order ID: {original_order_id}).")

        enriched_pnl_data = pnl_record.copy()
        enriched_pnl_data['alert_id'] = alert_id
        
        log_real_trade_result(enriched_pnl_data, active_order_data)
        processed_count += 1
        
        state_manager.delete_active_order_by_id(original_order_id)

        updated_time_ms = int(pnl_record.get("updatedTime", 0))
        if updated_time_ms > 0:
            record_ts = datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc)
            if record_ts > new_max_ts:
                new_max_ts = record_ts
    
    if new_max_ts > last_check_ts_dt:
        logger.info(f"[PNL_LOGGER] Zapisuję nowy timestamp ostatniego sprawdzenia: {new_max_ts.isoformat()}")
        save_last_processed_timestamp(new_max_ts + timedelta(seconds=1), "pnl_logger_last_fetch_state")
        
    logger.info(f"[PNL_LOGGER] Zakończono cykl. Przetworzono i zalogowano {processed_count} rekordów.")
    return processed_count```