# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Any, Dict, Optional
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


def release_lock_for_order(order_id: str) -> None:
    """Usuwa blokadę w processed_pnl_ids — wywołaj po nieudanym zapisie do BigQuery, żeby kolejny /log-pnl mógł ponowić próbę."""
    try:
        db = get_db()
        doc_ref = db.collection(constants.PROCESSED_ORDER_IDS_COLLECTION).document(order_id)
        doc_ref.delete()
        logger.info(f"[PNL_LOCK] Zwolniono blokadę dla order_id={order_id} (możliwa ponowna próba zapisu).")
    except Exception as e:
        logger.error(f"[PNL_LOCK] Nie udało się zwolnić blokady dla order_id={order_id}: {e}", exc_info=True)


def _ms_timestamp_to_iso(ts_raw: Any) -> str:
    """Bybit zwraca createdTime/updatedTime w ms (string lub int)."""
    if ts_raw is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        ms = float(ts_raw)
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def determine_exit_type(
    direction: str,
    avg_exit_price: float,
    planned_tp_price: float,
    planned_sl_price: float,
    bybit_close_type: Optional[str] = None,
) -> str:
    """
    Determine exit type from actual exit price vs planned levels.
    Do not blindly trust Bybit's closeType / exitType fields.
    """
    tolerance = 0.001  # 0.1% tolerance for slippage
    side = str(direction).upper()

    if side == "LONG":
        tp_hit = avg_exit_price >= planned_tp_price * (1 - tolerance)
        sl_hit = avg_exit_price <= planned_sl_price * (1 + tolerance)
    elif side == "SHORT":
        tp_hit = avg_exit_price <= planned_tp_price * (1 + tolerance)
        sl_hit = avg_exit_price >= planned_sl_price * (1 - tolerance)
    else:
        logger.warning(
            f"EXIT TYPE UNKNOWN: unsupported direction={direction!r}, exit={avg_exit_price}, "
            f"planned_tp={planned_tp_price}, planned_sl={planned_sl_price}. "
            f"Bybit closeType={bybit_close_type}"
        )
        return bybit_close_type or "Unknown"

    if tp_hit:
        return "TakeProfit"
    if sl_hit:
        return "StopLoss"

    logger.warning(
        f"EXIT BETWEEN LEVELS: direction={side}, exit={avg_exit_price}, "
        f"planned_tp={planned_tp_price}, planned_sl={planned_sl_price}. "
        f"Bybit closeType={bybit_close_type}"
    )
    return bybit_close_type or "Unknown"


def _signal_ts_for_bq(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


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

        def _normalize_exit_type(raw_value: Any) -> Optional[str]:
            if raw_value is None:
                return None
            text = str(raw_value).strip().lower()
            if not text:
                return None
            if "takeprofit" in text or text == "tp":
                return "TakeProfit"
            if "stoploss" in text or text == "sl":
                return "StopLoss"
            return None

        ao = active_order_data or {}

        # Używamy Decimal do precyzyjnych obliczeń wewnętrznych
        qty = Decimal(pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(pnl_data.get("avgExitPrice", "0.0"))
        net_pnl = Decimal(pnl_data.get("closedPnl") or "0.0")
        commission = Decimal(pnl_data.get("cumExecFee") or pnl_data.get("cumCommission") or "0.0")

        entry_value_usdt = qty * avg_entry_price
        exit_value_usdt = qty * avg_exit_price
        gross_pnl_usdt = net_pnl + commission

        planned_risk_usdt = None
        realized_rrr = None
        exit_price_result = None
        bybit_exit_type = (
            _normalize_exit_type(pnl_data.get("exitType"))
            or _normalize_exit_type(pnl_data.get("stopOrderType"))
            or _normalize_exit_type(pnl_data.get("orderType"))
        )
        exit_type = bybit_exit_type
        planned_tp_price_dec = None
        planned_sl_price_dec = None

        if is_matched:
            planned_sl_price = active_order_data.get("planned_sl_price")
            planned_tp_price = active_order_data.get("planned_tp_price")
            if planned_tp_price is not None:
                try:
                    planned_tp_price_dec = Decimal(str(planned_tp_price))
                except Exception:
                    planned_tp_price_dec = None
            if planned_sl_price is not None:
                try:
                    planned_sl_price_dec = Decimal(str(planned_sl_price))
                except Exception:
                    planned_sl_price_dec = None
            if planned_sl_price:
                planned_sl_price_dec = Decimal(str(planned_sl_price))
                if planned_sl_price_dec > 0 and avg_entry_price > 0:
                    risk_per_unit = abs(avg_entry_price - planned_sl_price_dec)
                    planned_risk_usdt_dec = risk_per_unit * qty
                    if planned_risk_usdt_dec > 0:
                        realized_rrr_dec = (net_pnl / planned_risk_usdt_dec)
                        planned_risk_usdt = float(planned_risk_usdt_dec)
                        realized_rrr = float(realized_rrr_dec)

            if planned_tp_price_dec is not None and planned_sl_price_dec is not None:
                trade_direction = active_order_data.get("direction")
                if trade_direction:
                    exit_type = determine_exit_type(
                        direction=trade_direction,
                        avg_exit_price=float(avg_exit_price),
                        planned_tp_price=float(planned_tp_price_dec),
                        planned_sl_price=float(planned_sl_price_dec),
                        bybit_close_type=bybit_exit_type,
                    )
                elif exit_type is None:
                    exit_type = bybit_exit_type
            if exit_type == "TakeProfit":
                exit_price_result = active_order_data.get("planned_tp_price")
            elif exit_type == "StopLoss":
                exit_price_result = active_order_data.get("planned_sl_price")

        # Przygotowanie finalnego obiektu z zaokrąglaniem wszystkich pól NUMERIC
        commission_usdt = safe_round(float(commission))
        if commission_usdt is None:
            commission_usdt = 0.0
        net_pnl_usdt = safe_round(float(net_pnl))
        if net_pnl_usdt is None:
            net_pnl_usdt = 0.0
        event_id = (active_order_data.get('event_id') if is_matched else None) or f"UNMATCHED-{order_id}"
        timestamp_signal = _signal_ts_for_bq(ao.get("timestamp")) or datetime.utcnow().isoformat() + "Z"

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
            "commission_usdt": commission_usdt,
            "net_pnl_usdt": net_pnl_usdt,
            "exit_type": exit_type,
            "timestamp_entry": _ms_timestamp_to_iso(pnl_data.get("createdTime")),
            "timestamp_close": _ms_timestamp_to_iso(pnl_data.get("updatedTime")),
            "planned_risk_usdt": safe_round(planned_risk_usdt),
            "realized_rrr": safe_round(realized_rrr, 4),  # RRR z mniejszą precyzją
            "alert_entry_price": safe_round(ao.get("planned_entry_price")) if active_order_data else None,
            "alert_sl_price": safe_round(ao.get("planned_sl_price")) if active_order_data else None,
            "alert_tp_price": safe_round(ao.get("planned_tp_price")) if active_order_data else None,
            "planned_entry_price": safe_round(ao.get("planned_entry_price")) if active_order_data else None,
            "planned_sl_price": safe_round(ao.get("planned_sl_price")) if active_order_data else None,
            "planned_tp_price": safe_round(ao.get("planned_tp_price")) if active_order_data else None,
            "exit_price_result": safe_round(exit_price_result),
            "tp_price_chart": safe_round(active_order_data.get("planned_tp_price")) if active_order_data else None,
            "event_id": event_id,
            "signal_id": ao.get("signal_id") if active_order_data else None,
            "timestamp_signal": timestamp_signal,
        }

    except Exception as e:
        logger.error(f"{log_prefix} Błąd podczas transformacji danych PnL: {e}", exc_info=True)
        return False

    if not acquire_lock_for_order(order_id):
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
            release_lock_for_order(order_id)
            return False

    except Exception as e:
        logger.critical(f"{log_prefix} Krytyczny błąd podczas zapisu do BigQuery: {e}", exc_info=True)
        release_lock_for_order(order_id)
        return False