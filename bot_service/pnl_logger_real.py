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

# --- POPRAWKA 1: Pełna synchronizacja schematu z definicją tabeli w BigQuery ---
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
    bigquery.SchemaField("planned_risk_usdt", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("realized_rrr", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("entry_price_alert", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("sl_price_alert", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("tp_price_alert", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("exit_price_result", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("tp_price_chart", "NUMERIC", mode="NULLABLE"),
]


def log_real_trade_result(enriched_pnl_data: Dict[str, Any], active_order_data: Dict[str, Any]):
    if not initialize_bigquery():
        logger.error("[PNL_LOGGER] BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return

    alert_id = enriched_pnl_data.get("alert_id", "unknown")
    order_id = enriched_pnl_data.get("orderId", "unknown")
    log_prefix = f"[PNL_LOGGER][{alert_id}]"

    try:
        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(enriched_pnl_data.get("avgExitPrice", "0.0"))
        net_pnl = Decimal(enriched_pnl_data.get("closedPnl") or "0.0")
        commission = Decimal(enriched_pnl_data.get("cumCommission") or "0.0")
        leverage = int(float(enriched_pnl_data.get("leverage", "1")))

        entry_value = qty * avg_entry_price
        exit_value = qty * avg_exit_price
        gross_pnl = net_pnl + commission

        # --- POPRAWKA 2: Pobieranie wszystkich potrzebnych danych z active_order_data ---
        entry_price_from_order = active_order_data.get("final_entry_price")
        sl_price_from_order = active_order_data.get("final_sl_price")
        tp_price_from_order = active_order_data.get("final_tp_price")
        tp_price_chart_from_order = active_order_data.get("tp_price_chart")

        sl_price_final = Decimal(str(sl_price_from_order)) if sl_price_from_order is not None else Decimal("0.0")

        planned_risk_usdt = Decimal("0.0")
        realized_rrr = Decimal("0.0")

        if sl_price_final > 0 and avg_entry_price > 0:
            risk_per_unit = abs(avg_entry_price - sl_price_final)
            planned_risk_usdt = risk_per_unit * qty
            
            if planned_risk_usdt > 0:
                realized_rrr = (net_pnl / planned_risk_usdt)
        
        PRECISION = Decimal('0.00000001')

        final_direction = "UNKNOWN"
        if active_order_data.get("direction"):
            final_direction = active_order_data.get("direction")
        elif enriched_pnl_data.get("side") == "Buy":
            final_direction = "LONG"
        elif enriched_pnl_data.get("side") == "Sell":
            final_direction = "SHORT"

        # --- POPRAWKA 3: Budowanie obiektu `transformed_data` ze wszystkimi wymaganymi polami ---
        transformed_data = {
            "alert_id": alert_id,
            "order_id": order_id,
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": final_direction,
            "qty": float(qty.quantize(PRECISION)),
            "leverage": leverage,
            "avg_entry_price": float(avg_entry_price.quantize(PRECISION)),
            "avg_exit_price": float(avg_exit_price.quantize(PRECISION)),
            "entry_value_usdt": float(entry_value.quantize(PRECISION)),
            "exit_value_usdt": float(exit_value.quantize(PRECISION)),
            "gross_pnl_usdt": float(gross_pnl.quantize(PRECISION)),
            "commission_usdt": float(commission.quantize(PRECISION)),
            "net_pnl_usdt": float(net_pnl.quantize(PRECISION)),
            "exit_type": enriched_pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "planned_risk_usdt": float(planned_risk_usdt.quantize(PRECISION)) if planned_risk_usdt > 0 else None,
            "realized_rrr": float(realized_rrr.quantize(PRECISION)) if planned_risk_usdt > 0 else None,
            
            # Dodane/poprawione pola, aby pasowały do schematu
            "entry_price_alert": float(entry_price_from_order) if entry_price_from_order is not None else None,
            "sl_price_alert": float(sl_price_from_order) if sl_price_from_order is not None else None,
            "tp_price_alert": float(tp_price_from_order) if tp_price_from_order is not None else None,
            "exit_price_result": float(avg_exit_price.quantize(PRECISION)), # Zgodnie z definicją, to rzeczywista cena zamknięcia
            "tp_price_chart": float(tp_price_chart_from_order) if tp_price_chart_from_order is not None else None,
        }
    except (TypeError, ValueError, KeyError) as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return

    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if not errors:
            logger.info(f"{log_prefix} SUKCES! Pomyślnie zapisano realny wynik transakcji do BigQuery.")
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania wierszy do BigQuery: {errors}")
    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)


