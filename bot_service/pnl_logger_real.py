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
    Wersja z kompleksowym zaokrąglaniem wszystkich wartości NUMERIC.
    """
    order_id = pnl_data.get("orderId", f"unknown_{int(datetime.now().timestamp())}")
    symbol = pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_SAVE][{symbol}|{order_id}]"

    if not bigquery_logger.initialize_bigquery():
        logger.error(f"{log_prefix} BigQuery nie zostało zainicjalizowane – pomijam zapis.")
        return False

    if not acquire_lock_for_order(order_id):
        return False

    # Używamy 'event_id' jako klucza dopasowania (zapis w active_orders z handle_immediate_signal)
    is_matched = bool(active_order_data and 'event_id' in active_order_data)
    alert_id = active_order_data.get('event_id', 'UNMATCHED_OR_MANUAL') if active_order_data else 'UNMATCHED_OR_MANUAL'
    
    if not is_matched:
        logger.warning(f"{log_prefix} ⚠️ Transakcja UNMATCHED – zapisuję z oznaczeniem.")
    else:
        logger.info(f"{log_prefix} ✅ Zlecenie dopasowane (event_id: {alert_id}).")

    try:
        # --- Funkcja pomocnicza do bezpiecznego zaokrąglania ---
        def safe_round(value, precision=6):
            if value is None:
                return None
            try:
                return round(float(value), precision)
            except (ValueError, TypeError):
                return None

        # Używamy Decimal do precyzyjnych obliczeń wewnętrznych
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

        # Przygotowanie finalnego obiektu z zaokrąglaniem wszystkich pól NUMERIC
        transformed_data = {
            "alert_id": alert_id,
            "order_id": order_id,
            "symbol": symbol,
            
            "direction": (
                active_order_data.get("direction") if is_matched 
                else ("SHORT" if float(pnl_data.get("avgEntryPrice", 0)) > float(pnl_data.get("avgExitPrice", 0)) else "LONG") 
                if float(pnl_data.get("closedPnl", 0)) > 0 
                else ("LONG" if float(pnl_data.get("avgEntryPrice", 0)) > float(pnl_data.get("avgExitPrice", 0)) else "SHORT")
            ),
            "qty": float(qty),
            "leverage": int(float(pnl_data.get("leverage", 0))) if pnl_data.get("leverage") else None,
            "avg_entry_price": safe_round(float(avg_entry_price)),
            "avg_exit_price": safe_round(float(avg_exit_price)),
            "entry_value_usdt": safe_round(float(entry_value_usdt)) if entry_value_usdt > 0 else None,
            "exit_value_usdt": safe_round(float(exit_value_usdt)) if exit_value_usdt > 0 else None,
            "gross_pnl_usdt": safe_round(float(gross_pnl_usdt)),
            "commission_usdt": safe_round(float(commission)),
            "net_pnl_usdt": safe_round(float(net_pnl)),
            "exit_type": pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "planned_risk_usdt": safe_round(planned_risk_usdt),
            "realized_rrr": safe_round(realized_rrr, 4), # RRR z mniejszą precyzją
            
            # --- MAPOWANIE DANYCH Z SIERRY (z active_order_data) ---
            "alert_entry_price": safe_round(active_order_data.get("entry")) if active_order_data else None,
            "alert_sl_price": safe_round(active_order_data.get("sl")) if active_order_data else None,
            "alert_tp_price": safe_round(active_order_data.get("tp")) if active_order_data else None,
            "timestamp_signal": active_order_data.get("timestamp") if active_order_data else None, # NOWE POLE
            
            # Planned (ceny po zaokrągleniu)
            "planned_entry_price": safe_round(active_order_data.get("planned_entry_price")) if active_order_data else None,
            "planned_sl_price": safe_round(active_order_data.get("planned_sl_price")) if active_order_data else None,
            "planned_tp_price": safe_round(active_order_data.get("planned_tp_price")) if active_order_data else None,
            "exit_price_result": safe_round(exit_price_result),
            "tp_price_chart": safe_round(active_order_data.get("alert_tp_price")) if active_order_data else None,
            
            # Pola analityczne (pobierane z active_order_data, jeśli zostały tam zapisane w Kroku 4)
            "m2_delta": safe_round(active_order_data.get("m2_delta", 0.0)),
            "m5_rs_ratio": safe_round(active_order_data.get("m5_rs_ratio", 0.0), 4),
            "structure_state": active_order_data.get("structure_state"),
            "bos_high": active_order_data.get("bos_high"),
            "bos_low": active_order_data.get("bos_low"),
            "choch_up": active_order_data.get("choch_up"),
            "choch_down": active_order_data.get("choch_down"),
            "liquidity_grab_above": active_order_data.get("liquidity_grab_above"),
            "liquidity_grab_below": active_order_data.get("liquidity_grab_below"),
            "liquidity_price": active_order_data.get("liquidity_price"),
            "eqh_detected": active_order_data.get("eqh_detected"),
            "eql_detected": active_order_data.get("eql_detected"),
            "risk_usdt": active_order_data.get("risk_usdt"),
            "session": active_order_data.get("session"),
            "minute_of_day": active_order_data.get("minute_of_day"),
            "day_of_week": active_order_data.get("day_of_week"),
            "second": active_order_data.get("second"),
            "bar_range": active_order_data.get("bar_range"),
            "ob_range": active_order_data.get("ob_range"),
            "swing_range": active_order_data.get("swing_range"),
            "distance_to_liquidity": active_order_data.get("distance_to_liquidity"),
            "volatility_regime": active_order_data.get("volatility_regime"),
            "raw_context": active_order_data.get("raw_context"),
        }

    except Exception as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return False

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
                    logger.error(f"{log_prefix} Nie można usunąć dokumentu – brak pola 'id' w dopasowanych danych.")
            return True
        else:
            logger.error(f"{log_prefix} Błąd podczas wstawiania do BigQuery: {errors}. Dokument w active_orders NIE został usunięty.")
            return False

    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)
        return False