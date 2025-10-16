# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Dict, Any
from datetime import datetime, timezone
from google.cloud import bigquery
from decimal import Decimal

from bot_service import bigquery_logger, state_manager
from shared_lib import constants

logger = logging.getLogger(__name__)

# Nowa kolekcja w Firestore do śledzenia przetworzonych ID
PROCESSED_IDS_COLLECTION = "processed_order_ids"

def check_if_order_processed(order_id: str) -> bool:
    """
    Nowa, niezawodna funkcja do sprawdzania duplikatów.
    Krok 1: Sprawdza szybki cache w Firestore.
    Krok 2: Jeśli nie ma w cache'u, sprawdza BigQuery jako zabezpieczenie.
    """
    # Krok 1: Sprawdzenie w cache'u Firestore
    try:
        db = state_manager._get_db()
        doc_ref = db.collection(PROCESSED_IDS_COLLECTION).document(order_id)
        if doc_ref.get().exists:
            logger.warning(f"[PNL_DUPLICATE_CHECK][CACHE] Znaleziono wpis dla order_id: {order_id} w cache'u Firestore. Pomijam.")
            return True
    except Exception as e:
        logger.error(f"[PNL_DUPLICATE_CHECK][CACHE] Błąd podczas sprawdzania cache'a dla order_id {order_id}: {e}", exc_info=True)
        # W przypadku błędu cache'a, dla bezpieczeństwa przechodzimy do sprawdzenia BQ
        pass

    # Krok 2: Sprawdzenie w BigQuery (jako druga linia obrony)
    try:
        client = bigquery_logger.get_bigquery_client()
        query = f"""
            SELECT COUNT(1) as count
            FROM `{bigquery_logger.REAL_TRADES_TABLE_REF.project}.{bigquery_logger.REAL_TRADES_TABLE_REF.dataset_id}.{bigquery_logger.REAL_TRADES_TABLE_REF.table_id}`
            WHERE order_id = @order_id
        """
        query_params = [bigquery.ScalarQueryParameter("order_id", "STRING", order_id)]
        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()
        
        for row in results:
            if row.count > 0:
                logger.warning(f"[PNL_DUPLICATE_CHECK][BQ] Znaleziono wpis dla order_id: {order_id} w BigQuery. Pomijam zapis i aktualizuję cache.")
                # Jeśli znaleziono w BQ, ale nie w cache'u, naprawiamy cache
                mark_order_as_processed(order_id)
                return True
        return False
    except Exception as e:
        logger.error(f"[PNL_DUPLICATE_CHECK][BQ] Błąd podczas sprawdzania istnienia order_id {order_id} w BQ: {e}", exc_info=True)
        # Jeśli nawet sprawdzenie BQ zawiedzie, na wszelki wypadek nie zapisujemy
        return True

def mark_order_as_processed(order_id: str):
    """Zapisuje ID zlecenia w cache'u Firestore, aby oznaczyć je jako przetworzone."""
    try:
        db = state_manager._get_db()
        doc_ref = db.collection(PROCESSED_IDS_COLLECTION).document(order_id)
        doc_ref.set({"processed_at": datetime.now(timezone.utc)})
        logger.info(f"[PNL_CACHE_UPDATE] Pomyślnie oznaczono order_id {order_id} jako przetworzony w cache'u.")
    except Exception as e:
        logger.error(f"[PNL_CACHE_UPDATE] Błąd podczas oznaczania order_id {order_id} jako przetworzony: {e}", exc_info=True)


def log_real_trade_result(enriched_pnl_data: Dict[str, Any], active_order_data: Dict[str, Any]):
    order_id = enriched_pnl_data.get("orderId", "unknown")
    symbol = enriched_pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_REAL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return

    # --- UŻYCIE NOWEJ, NIEZAWODNEJ FUNKCJI DEDUPLIKACJI ---
    if check_if_order_processed(order_id):
        return
    # --- KONIEC ZMIANY ---

    logger.info(
        f"{log_prefix} Otrzymano dane do przetworzenia i zapisu.", 
        extra={"json_fields": {
            "pnl_data": enriched_pnl_data,
            "active_order_data": active_order_data
        }}
    )

    try:
        if enriched_pnl_data.get('avgEntryPrice') is None and active_order_data.get('final_entry_price'):
            enriched_pnl_data['avgEntryPrice'] = active_order_data['final_entry_price']
            logger.warning(f"{log_prefix} Uzupełniono brakującą cenę wejścia z danych zlecenia (prawdopodobnie likwidacja).")

        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(enriched_pnl_data.get("avgExitPrice", "0.0"))
        net_pnl = Decimal(enriched_pnl_data.get("closedPnl") or "0.0")
        commission = Decimal(enriched_pnl_data.get("cumCommission") or "0.0")
        leverage_str = enriched_pnl_data.get("leverage")
        leverage = int(float(leverage_str)) if leverage_str else None

        entry_value = qty * avg_entry_price
        exit_value = qty * avg_exit_price
        gross_pnl = net_pnl + commission

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

        transformed_data = {
            "alert_id": enriched_pnl_data.get("alert_id", "unknown"),
            "order_id": order_id,
            "symbol": symbol,
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
            "entry_price_alert": float(entry_price_from_order) if entry_price_from_order is not None else None,
            "sl_price_alert": float(sl_price_from_order) if sl_price_from_order is not None else None,
            "tp_price_alert": float(tp_price_from_order) if tp_price_from_order is not None else None,
            "exit_price_result": float(avg_exit_price.quantize(PRECISION)),
            "tp_price_chart": float(tp_price_chart_from_order) if tp_price_chart_from_order is not None else None,
        }
    except (TypeError, ValueError, KeyError) as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return

    try:
        client = bigquery_logger.get_bigquery_client()
        logger.info(
            f"{log_prefix} Przygotowano dane do zapisu w BigQuery. Próba wstawienia...", 
            extra={"json_fields": {"bq_payload": transformed_data}}
        )
        
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            logger.info(f"{log_prefix} SUKCES! Pomyślnie zapisano realny wynik transakcji do BigQuery.")
            # --- AKTUALIZACJA CACHE'A PO POMYŚLNYM ZAPISIE ---
            mark_order_as_processed(order_id)
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania wierszy do BigQuery: {errors}")
    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)