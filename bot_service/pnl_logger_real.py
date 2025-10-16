# Lokalizacja: bot_service/pnl_logger_real.py

# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from google.cloud import bigquery, firestore
from decimal import Decimal

from bot_service import bigquery_logger, state_manager
from shared_lib import constants

logger = logging.getLogger(__name__)

PROCESSED_IDS_COLLECTION = "processed_order_ids"

def acquire_lock_for_order(order_id: str) -> bool:
    """
    Próbuje atomowo utworzyć dokument blokady w Firestore.
    Zwraca True, jeśli blokada została pomyślnie założona (tzn. ten proces wygrał wyścig).
    Zwraca False, jeśli blokada już istniała.
    """
    try:
        db = state_manager._get_db()
        doc_ref = db.collection(PROCESSED_IDS_COLLECTION).document(order_id)
        
        @firestore.transactional
        def _create_if_not_exists(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            else:
                transaction.set(doc_ref, {"processed_at": datetime.now(timezone.utc), "status": "PROCESSING"})
                return True

        transaction = db.transaction()
        lock_acquired = _create_if_not_exists(transaction, doc_ref)

        if lock_acquired:
            logger.info(f"[PNL_LOCK] Pomyślnie założono blokadę dla order_id: {order_id}")
            return True
        else:
            logger.warning(f"[PNL_DUPLICATE_CHECK][CACHE] Blokada dla order_id: {order_id} już istnieje. Pomijam.")
            return False
            
    except Exception as e:
        logger.error(f"[PNL_LOCK] Błąd podczas próby założenia blokady dla order_id {order_id}: {e}", exc_info=True)
        return False

def mark_lock_as_done(order_id: str):
    """Aktualizuje status blokady na 'DONE' po pomyślnym zapisie do BigQuery."""
    try:
        db = state_manager._get_db()
        doc_ref = db.collection(PROCESSED_IDS_COLLECTION).document(order_id)
        doc_ref.update({"status": "DONE"})
        logger.info(f"[PNL_LOCK_UPDATE] Pomyślnie zaktualizowano status blokady dla order_id {order_id}.")
    except Exception as e:
        logger.error(f"[PNL_LOCK_UPDATE] Błąd podczas aktualizacji statusu blokady dla order_id {order_id}: {e}", exc_info=True)

def log_real_trade_result(enriched_pnl_data: Dict[str, Any], active_order_data: Optional[Dict[str, Any]]) -> bool:
    """
    Przetwarza, transformuje i zapisuje wynik transakcji do BigQuery.
    Implementuje logikę blokowania, aby zapobiec duplikatom.

    Returns:
        bool: True, jeśli rekord został pomyślnie zapisany.
              False, jeśli rekord był duplikatem lub wystąpił błąd.
    """
    if active_order_data is None:
        active_order_data = {}
        
    order_id = enriched_pnl_data.get("orderId", "unknown")
    symbol = enriched_pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_REAL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return False

    if not acquire_lock_for_order(order_id):
        return False

    alert_id = active_order_data.get('alert_id', 'UNMATCHED_OR_MANUAL')
    
    logger.info(
        f"{log_prefix} Otrzymano nowe dane do przetworzenia i zapisu (alert_id: {alert_id}).", 
        extra={"json_fields": {"pnl_data": enriched_pnl_data, "active_order_data": active_order_data}}
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
            "alert_id": alert_id,
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
        return False

    try:
        client = bigquery_logger.get_bigquery_client()
        logger.info(
            f"{log_prefix} Przygotowano dane do zapisu w BigQuery. Próba wstawienia...", 
            extra={"json_fields": {"bq_payload": transformed_data}}
        )
        
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            logger.info(f"{log_prefix} SUKCES! Pomyślnie zapisano realny wynik transakcji do BigQuery.")
            mark_lock_as_done(order_id)
            return True
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania wierszy do BigQuery: {errors}")
            return False
    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)
        return False