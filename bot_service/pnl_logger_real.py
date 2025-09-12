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

# Lokalizacja: bot_service/pnl_logger_real.py

def log_real_trade_result(enriched_pnl_data: Dict[str, Any]):
    """Transformuje wzbogacone dane PnL z Bybit, wzbogaca je o dane z alertu, oblicza R:R i zapisuje do BigQuery."""
    
    if not initialize_bigquery():
        logger.error("BigQuery nie zostało zainicjalizowane – pomijam zapis real_trades_history.")
        return

    alert_id = enriched_pnl_data.get("alert_id", "unknown")
    
    original_alert_data = state_manager.get_alert_data_by_id(alert_id)
    if not original_alert_data:
        logger.error(f"Nie można w pełni wzbogacić danych, ponieważ nie znaleziono oryginalnego alertu o ID: {alert_id}")
        original_alert_data = {}

    try:
        # --- Konwersja i walidacja danych z Bybit ---
        side = enriched_pnl_data.get("side")
        direction = "LONG" if side == "Buy" else "SHORT" if side == "Sell" else "UNKNOWN"

        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(enriched_pnl_data.get("avgExitPrice", "0.0"))
        commission = Decimal(enriched_pnl_data.get("cumCommission") or "0.0")
        net_pnl = Decimal(enriched_pnl_data.get("closedPnl") or "0.0")
        
        # --- Pobieranie planowanych cen z oryginalnego alertu ---
        entry_price_alert = Decimal(str(original_alert_data.get("entry", "0.0")))
        sl_price_alert = Decimal(str(original_alert_data.get("sl", "0.0")))
        # Zakładamy, że bot handluje na tp_3_0, zgodnie z logiką w bot_logic.py
        tp_price_alert = Decimal(str(original_alert_data.get("tp_3_0", "0.0")))

        # --- Obliczenia R:R ---
        planned_risk_usdt = Decimal("0.0")
        realized_rrr = Decimal("0.0")

        if sl_price_alert > 0 and avg_entry_price > 0:
            risk_per_unit = abs(avg_entry_price - sl_price_alert)
            planned_risk_usdt = risk_per_unit * qty
            
            if planned_risk_usdt > 0:
                realized_rrr = (net_pnl / planned_risk_usdt).quantize(Decimal('0.0001'), rounding=ROUND_DOWN)
        
        # --- Budowanie ostatecznego obiektu do zapisu, zgodnego ze schematem ---
        transformed_data = {
            "alert_id": alert_id,
            "order_id": enriched_pnl_data.get("orderId", "unknown"),
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": direction,
            "qty": float(qty),
            "leverage": int(float(enriched_pnl_data.get("leverage", 1))),
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(avg_exit_price),
            "entry_value_usdt": float(qty * avg_entry_price),
            "exit_value_usdt": float(qty * avg_exit_price),
            "gross_pnl_usdt": float(net_pnl + commission),
            "commission_usdt": float(commission),
            "net_pnl_usdt": float(net_pnl),
            "exit_type": enriched_pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            
            # --- UZUPEŁNIONE POLA ZGODNIE ZE SCHEMATEM ---
            "planned_risk_usdt": float(planned_risk_usdt) if planned_risk_usdt > 0 else None,
            "realized_rrr": float(realized_rrr) if planned_risk_usdt > 0 else None,
            "entry_price_alert": float(entry_price_alert) if entry_price_alert > 0 else None,
            "sl_price_alert": float(sl_price_alert) if sl_price_alert > 0 else None,
            "tp_price_alert": float(tp_price_alert) if tp_price_alert > 0 else None,
            "exit_price_result": float(avg_exit_price) if avg_exit_price > 0 else None,
        }
    except (TypeError, ValueError, KeyError) as e:
        logger.error(f"Błąd podczas transformacji danych PnL dla alertu {alert_id}: {e}", exc_info=True, extra={"json_fields": {"pnl_data": enriched_pnl_data}})
        return

    logger.info(f"Przygotowano dane do zapisu w BigQuery: {transformed_data}")
    
    try:
        client = get_bigquery_client()
        if not client:
            logger.error("Nie udało się uzyskać klienta BigQuery. Pomijam zapis.")
            return
            
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if not errors:
            logger.info(f"Pomyślnie zapisano realny wynik transakcji dla alertu {alert_id} do BigQuery.")
        else:
            logger.error(f"Błąd podczas wstawiania wierszy do BigQuery dla alertu {alert_id}: {errors}")
    except Exception as e:
        logger.critical(f"Krytyczny błąd podczas zapisu do BigQuery dla alertu {alert_id}: {e}", exc_info=True)