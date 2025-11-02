# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Dict, Any, Optional
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


def log_real_trade_result(pnl_data: Dict[str, Any], active_order_data: Optional[Dict[str, Any]]) -> bool:
    """
    Zapisuje wynik rzeczywistej transakcji do BigQuery, dopasowując ją do aktywnego zlecenia.
    Wersja rozszerzona: próba automatycznego dopasowania zlecenia w przypadku UNMATCHED.
    """
    from bot_service import state_manager

    order_id = pnl_data.get("orderId", f"unknown_{int(datetime.now().timestamp())}")
    symbol = pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return False

    # --- 🔒 Zabezpieczenie przed duplikacją ---
    if not acquire_lock_for_order(order_id):
        return False

    # --- 🧠 AUTOMATYCZNE DOPASOWANIE, GDY BRAK ACTIVE_ORDER ---
    if not active_order_data:
        logger.warning(f"{log_prefix} Brak dopasowania – próbuję znaleźć ręcznie...")
        limit_order_id = pnl_data.get("orderLinkId") or pnl_data.get("orderId")
        if limit_order_id:
            active_order_data = state_manager.get_active_order_by_limit_order_id(limit_order_id)
        if not active_order_data and pnl_data.get("tpslOrderId"):
            active_order_data = state_manager.get_active_order_by_tpsl_order_id(pnl_data["tpslOrderId"])
        if not active_order_data and symbol:
            # fallback: dopasowanie po symbol + cena wejścia + ilość
            all_orders = list(state_manager.get_all_active_orders())
            for doc in all_orders:
                od = doc.to_dict()
                if (
                    od.get("symbol") == symbol
                    and abs(float(od.get("alert_entry_price", 0)) - float(pnl_data.get("avgEntryPrice", 0))) < 0.5
                    and abs(float(od.get("qty", 0)) - float(pnl_data.get("qty", 0))) < 0.0001
                ):
                    active_order_data = od
                    logger.info(f"{log_prefix} Znaleziono dopasowanie po symbolu i cenie wejścia.")
                    break

    # --- STATUS DOPASOWANIA ---
    is_matched = bool(active_order_data and 'alert_id' in active_order_data)
    alert_id = active_order_data.get('alert_id', 'UNMATCHED_OR_MANUAL') if active_order_data else 'UNMATCHED_OR_MANUAL'
    
    if not is_matched:
        logger.warning(f"{log_prefix} ⚠️ Transakcja UNMATCHED – zapisuję z oznaczeniem.")
    else:
        logger.info(f"{log_prefix} ✅ Zlecenie dopasowane (alert_id: {alert_id}).")

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
            if planned_sl_price:
                planned_sl_price_dec = Decimal(str(planned_sl_price))
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

        transformed_data = {
            "alert_id": alert_id,
            "order_id": order_id,
            "symbol": symbol,
            "direction": active_order_data.get("direction") if active_order_data else pnl_data.get("side"),
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
            "alert_entry_price": active_order_data.get("alert_entry_price") if active_order_data else None,
            "alert_sl_price": active_order_data.get("alert_sl_price") if active_order_data else None,
            "alert_tp_price": active_order_data.get("alert_tp_price") if active_order_data else None,
            "planned_entry_price": active_order_data.get("planned_entry_price") if active_order_data else None,
            "planned_sl_price": active_order_data.get("planned_sl_price") if active_order_data else None,
            "planned_tp_price": active_order_data.get("planned_tp_price") if active_order_data else None,
            "exit_price_result": exit_price_result,
            "tp_price_chart": active_order_data.get("alert_tp_price") if active_order_data else None,
        }

    except Exception as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return False

    # --- ZAPIS DO BIGQUERY ---
    try:
        client = bigquery_logger.get_bigquery_client()
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            logger.info(f"{log_prefix} ✅ Zapisano wynik transakcji do BigQuery.")
            if is_matched:
                order_link_id_to_delete = active_order_data.get('id')
                if order_link_id_to_delete:
                    logger.info(f"{log_prefix} Sprzątanie: Usuwam dokument '{order_link_id_to_delete}' z active_orders.")
                    state_manager.delete_active_order_by_id(order_link_id_to_delete)
                else:
                    logger.error(f"{log_prefix} Nie można usunąć dokumentu – brak pola 'id'.")
            return True
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania do BigQuery: {errors}. Dokument w active_orders NIE został usunięty.")
            return False

    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)
        return False
