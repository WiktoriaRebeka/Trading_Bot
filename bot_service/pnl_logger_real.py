# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Dict, Any
from datetime import datetime, timezone
from google.cloud import firestore
from decimal import Decimal, getcontext

from bot_service import bigquery_logger, state_manager
from shared_lib import constants
from shared_lib.firebase_client import get_db

logger = logging.getLogger(__name__)
getcontext().prec = 18

def acquire_lock_for_order(order_id: str) -> bool:
    try:
        db = get_db()
        doc_ref = db.collection(constants.PROCESSED_ORDER_IDS_COLLECTION).document(order_id)
        
        @firestore.transactional
        def _create_if_not_exists(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            else:
                transaction.set(doc_ref, {"processed_at": datetime.now(timezone.utc)})
                return True

        transaction = db.transaction()
        lock_acquired = _create_if_not_exists(transaction, doc_ref)

        if lock_acquired:
            logger.info(f"[PNL_LOCK] Pomyślnie założono blokadę dla order_id: {order_id}")
            return True
        else:
            logger.warning(f"[PNL_DUPLICATE] Blokada dla order_id: {order_id} już istnieje. Pomijam przetwarzanie.")
            return False
            
    except Exception as e:
        logger.error(f"[PNL_LOCK] Błąd podczas próby założenia blokady dla order_id {order_id}: {e}", exc_info=True)
        return False

# Lokalizacja: bot_service/pnl_logger_real.py
# ZASTĄP FUNKCJĘ 'log_real_trade_result' PONIŻSZĄ WERSJĄ

def log_real_trade_result(pnl_data: Dict[str, Any], active_order_data: Dict[str, Any]) -> bool:
    order_id = pnl_data.get("orderId", f"unknown_{int(datetime.now().timestamp())}")
    symbol = pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return False

    if not acquire_lock_for_order(order_id):
        return False

    is_matched = bool(active_order_data and 'alert_id' in active_order_data)
    alert_id = active_order_data.get('alert_id', 'UNMATCHED_OR_MANUAL')
    
    if not is_matched:
        logger.warning(f"{log_prefix} Nie znaleziono dopasowania. Transakcja zostanie zapisana jako UNMATCHED.")
    else:
        logger.info(f"{log_prefix} Rozpoczynam transakcyjny zapis (alert_id: {alert_id}).")

    try:
        qty = Decimal(pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(pnl_data.get("avgExitPrice", "0.0"))
        net_pnl = Decimal(pnl_data.get("closedPnl") or "0.0")
        commission = Decimal(pnl_data.get("cumCommission") or "0.0")
        
        entry_value_usdt = qty * avg_entry_price
        exit_value_usdt = qty * avg_exit_price
        gross_pnl_usdt = net_pnl + commission

        planned_risk_usdt = None
        realized_rrr = None
        exit_price_result = None

        if is_matched:
            planned_sl_price = active_order_data.get("planned_sl_price")
            planned_sl_price_dec = Decimal(str(planned_sl_price)) if planned_sl_price is not None else Decimal("0.0")

            if planned_sl_price_dec > 0 and avg_entry_price > 0:
                risk_per_unit = abs(avg_entry_price - planned_sl_price_dec)
                planned_risk_usdt_dec = risk_per_unit * qty
                
                if planned_risk_usdt_dec > 0:
                    realized_rrr_dec = (net_pnl / planned_risk_usdt_dec)
                    planned_risk_usdt = float(planned_risk_usdt_dec)
                    realized_rrr = float(realized_rrr_dec)
            
            exit_type = pnl_data.get("exitType")
            if exit_type == "TakeProfit":
                exit_price_result = active_order_data.get("planned_tp_price")
            elif exit_type == "StopLoss":
                exit_price_result = active_order_data.get("planned_sl_price")

        # --- FINALNA, ZGODNA ZE SCHEMATEM STRUKTURA ---
        transformed_data = {
            "alert_id": alert_id,
            "order_id": order_id,
            "symbol": symbol,
            "direction": active_order_data.get("direction", pnl_data.get("side")),
            "qty": float(qty),
            "leverage": int(float(pnl_data.get("leverage", 0))) or None,
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(avg_exit_price),
            "entry_value_usdt": float(entry_value_usdt) if entry_value_usdt > 0 else None,
            "exit_value_usdt": float(exit_value_usdt) if exit_value_usdt > 0 else None,
            "gross_pnl_usdt": float(gross_pnl_usdt) if gross_pnl_usdt != 0 else None,
            "commission_usdt": float(commission),
            "net_pnl_usdt": float(net_pnl),
            "exit_type": pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "planned_risk_usdt": planned_risk_usdt,
            "realized_rrr": realized_rrr,
            "alert_entry_price": active_order_data.get("alert_entry_price"),
            "alert_sl_price": active_order_data.get("alert_sl_price"),
            "alert_tp_price": active_order_data.get("alert_tp_price"),
            "planned_entry_price": active_order_data.get("planned_entry_price"),
            "planned_sl_price": active_order_data.get("planned_sl_price"),
            "planned_tp_price": active_order_data.get("planned_tp_price"),
            "exit_price_result": exit_price_result,
            "tp_price_chart": active_order_data.get("alert_tp_price"),
        }
    except Exception as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return False

    try:
        client = bigquery_logger.get_bigquery_client()
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            logger.info(f"{log_prefix} SUKCES! Pomyślnie zapisano wynik transakcji do BigQuery.")
            
            if is_matched:
                order_link_id_to_delete = active_order_data.get('id')
                if order_link_id_to_delete:
                    logger.info(f"{log_prefix} Sprzątanie: Usuwanie dokumentu '{order_link_id_to_delete}' z kolekcji active_orders.")
                    state_manager.delete_active_order_by_id(order_link_id_to_delete)
                else:
                    logger.error(f"{log_prefix} BŁĄD KRYTYCZNY: Nie można usunąć rekordu, ponieważ 'id' (orderLinkId) nie zostało znalezione w dopasowanych danych.")
            
            return True
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania wierszy do BigQuery: {errors}. Dokument w active_orders NIE został usunięty.")
            return False
    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}. Dokument w active_orders NIE został usunięty.", exc_info=True)
        return False