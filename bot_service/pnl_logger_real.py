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


def log_real_trade_result(enriched_pnl_data: Dict[str, Any], active_order_data: Dict[str, Any]):
    """Transformuje dane PnL, wzbogaca je o DOKŁADNE dane zlecenia i zapisuje do BigQuery."""
    if not initialize_bigquery():
        logger.error("BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return

    alert_id = enriched_pnl_data.get("alert_id", "unknown")

    try:
        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        net_pnl = Decimal(enriched_pnl_data.get("closedPnl") or "0.0")
        
        # Używamy FINALNYCH, ZAOKRĄGLONYCH cen z `active_order_data`
        sl_price_final = Decimal(str(active_order_data.get("final_sl_price", "0.0")))

        planned_risk_usdt = Decimal("0.0")
        realized_rrr = Decimal("0.0")

        if sl_price_final > 0 and avg_entry_price > 0:
            risk_per_unit = abs(avg_entry_price - sl_price_final)
            planned_risk_usdt = risk_per_unit * qty
            
            if planned_risk_usdt > 0:
                realized_rrr = (net_pnl / planned_risk_usdt).quantize(Decimal('0.0001'))
        
        transformed_data = {
            "alert_id": alert_id,
            "order_id": enriched_pnl_data.get("orderId", "unknown"),
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": "LONG" if enriched_pnl_data.get("side") == "Buy" else "SHORT",
            "qty": float(qty),
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(enriched_pnl_data.get("avgExitPrice", "0.0")),
            "net_pnl_usdt": float(net_pnl),
            "commission_usdt": float(enriched_pnl_data.get("cumCommission") or "0.0"),
            "exit_type": enriched_pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "planned_risk_usdt": float(planned_risk_usdt) if planned_risk_usdt > 0 else None,
            "realized_rrr": float(realized_rrr) if planned_risk_usdt > 0 else None,
            "sl_price_alert": float(sl_price_final) if sl_price_final > 0 else None,
            "tp_price_alert": float(active_order_data.get("final_tp_price")) if active_order_data.get("final_tp_price") else None,
            "tp_price_chart": float(active_order_data.get("tp_price_chart")) if active_order_data.get("tp_price_chart") else None,
        }
    except (TypeError, ValueError, KeyError) as e:
        logger.error(f"Błąd podczas transformacji danych PnL dla alertu {alert_id}: {e}", exc_info=True)
        return

    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if not errors:
            logger.info(f"Pomyślnie zapisano realny wynik transakcji dla alertu {alert_id} do BigQuery.")
        else:
            logger.error(f"Błąd podczas wstawiania wierszy do BigQuery dla alertu {alert_id}: {errors}")
    except Exception as e:
        logger.critical(f"Krytyczny błąd podczas zapisu do BigQuery dla alertu {alert_id}: {e}", exc_info=True)