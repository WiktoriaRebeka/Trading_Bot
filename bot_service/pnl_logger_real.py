# Lokalizacja: bot_service/pnl_logger_real.py

import logging
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timezone
from decimal import Decimal, getcontext

from bot_service import bigquery_logger, state_manager

logger = logging.getLogger(__name__)
getcontext().prec = 18

_TICK_TOLERANCE_COUNT = 2
_instrument_rules_cache: Optional[Dict[str, Any]] = None


def _get_instrument_rules_cached() -> Dict[str, Any]:
    """Firestore instrument_rules — ten sam dokument co przy round_price_by_tick w bot_logic."""
    global _instrument_rules_cache
    if _instrument_rules_cache is None:
        try:
            from shared_lib.firebase_client import get_instrument_rules

            _instrument_rules_cache = get_instrument_rules() or {}
        except Exception as exc:
            logger.warning(f"Nie udało się wczytać instrument_rules z Firestore: {exc}")
            _instrument_rules_cache = {}
    return _instrument_rules_cache


def _tick_size_for_symbol(symbol: str) -> Optional[float]:
    rules_map = _get_instrument_rules_cached()
    symbol_upper = str(symbol).upper()
    candidates = [symbol_upper, symbol_upper.replace(".P", ""), f"{symbol_upper.replace('.P', '')}.P"]
    for key in candidates:
        rules = rules_map.get(key)
        if not rules:
            continue
        raw_tick = rules.get("tickSize")
        if raw_tick is None:
            continue
        try:
            tick = float(raw_tick)
            if tick > 0:
                return tick
        except (TypeError, ValueError):
            continue
    return None


def _infer_tick_from_prices(*prices: float) -> float:
    """Fallback: ~2 ticki z precyzji dziesiętnej cen planowanych."""
    max_decimals = 0
    for price in prices:
        if price is None or price <= 0:
            continue
        normalized = Decimal(str(price)).normalize()
        exponent = normalized.as_tuple().exponent
        if isinstance(exponent, int) and exponent < 0:
            max_decimals = max(max_decimals, -exponent)
    if max_decimals == 0:
        return 1e-4 * _TICK_TOLERANCE_COUNT
    return float(10 ** (-max_decimals)) * _TICK_TOLERANCE_COUNT


def resolve_exit_tick_tolerance(
    symbol: str,
    active_order_data: Optional[Dict[str, Any]],
    planned_tp_price: float,
    planned_sl_price: float,
    avg_exit_price: float,
) -> float:
    """
    Tolerancja = 2 × tickSize.
    Źródła tick (bez REST Bybit): pole zlecenia → Firestore instrument_rules → precyzja ceny.
    """
    ao = active_order_data or {}
    for key in ("tick_size", "tickSize"):
        raw = ao.get(key)
        if raw is not None:
            try:
                tick = float(raw)
                if tick > 0:
                    return tick * _TICK_TOLERANCE_COUNT
            except (TypeError, ValueError):
                pass

    tick = _tick_size_for_symbol(symbol)
    if tick is not None:
        return tick * _TICK_TOLERANCE_COUNT

    return _infer_tick_from_prices(planned_tp_price, planned_sl_price, avg_exit_price)


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
    tick_tolerance: float,
    net_pnl: Optional[float] = None,
    bybit_close_type: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    Klasyfikacja wyjścia na podstawie avg_exit_price vs planowane poziomy (tick-size tol).
    Zwraca (exit_type, at_level) — at_level=True gdy exit w tolerancji TP lub SL.
    """
    side = str(direction).upper()
    tol = tick_tolerance
    e, tp, sl = avg_exit_price, planned_tp_price, planned_sl_price

    if side == "LONG":
        tp_hit = e >= tp - tol
        sl_hit = e <= sl + tol
    elif side == "SHORT":
        tp_hit = e <= tp + tol
        sl_hit = e >= sl - tol
    else:
        logger.warning(
            f"EXIT TYPE UNKNOWN: unsupported direction={direction!r}, exit={e}, "
            f"planned_tp={tp}, planned_sl={sl}. Bybit closeType={bybit_close_type}"
        )
        return bybit_close_type or "Unknown", False

    at_level = tp_hit or sl_hit

    if tp_hit and sl_hit:
        resolved = "TakeProfit" if (net_pnl is not None and net_pnl >= 0) else "StopLoss"
        logger.warning(
            f"EXIT AMBIGUOUS (tp+sl in tolerance): direction={side}, exit={e}, "
            f"tp={tp}, sl={sl}, tol={tol}, net_pnl={net_pnl} → {resolved}"
        )
        return resolved, True

    if tp_hit:
        return "TakeProfit", True
    if sl_hit:
        return "StopLoss", True

    logger.info(
        f"EXIT BETWEEN LEVELS: direction={side}, exit={e}, tp={tp}, sl={sl}, "
        f"tol={tol} → Manual"
    )
    return "Manual", False


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
    Marker w processed_pnl_ids ustawiany jest dopiero po udanym insert_rows_json.
    """
    raw_oid = pnl_data.get("orderId")
    if not raw_oid:
        symbol = pnl_data.get("symbol", "unknown")
        logger.error(f"[PNL_SAVE][{symbol}|no_orderId] Brak orderId w rekordzie closed-pnl — pomijam zapis.")
        return False
    order_id = str(raw_oid)
    symbol = pnl_data.get("symbol", "unknown")
    log_prefix = f"[PNL_SAVE][{symbol}|{order_id}]"

    if state_manager.is_closed_pnl_record_logged(order_id):
        logger.info(f"{log_prefix} Już zapisane w BigQuery (processed_pnl_ids) — pomijam.")
        return False

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
        exit_price_result = safe_round(float(avg_exit_price))
        bybit_exit_type = (
            _normalize_exit_type(pnl_data.get("exitType"))
            or _normalize_exit_type(pnl_data.get("stopOrderType"))
            or _normalize_exit_type(pnl_data.get("orderType"))
        )
        exit_type = bybit_exit_type
        exit_at_level = False
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
                    tick_tol = resolve_exit_tick_tolerance(
                        symbol=symbol,
                        active_order_data=active_order_data,
                        planned_tp_price=float(planned_tp_price_dec),
                        planned_sl_price=float(planned_sl_price_dec),
                        avg_exit_price=float(avg_exit_price),
                    )
                    exit_type, exit_at_level = determine_exit_type(
                        direction=trade_direction,
                        avg_exit_price=float(avg_exit_price),
                        planned_tp_price=float(planned_tp_price_dec),
                        planned_sl_price=float(planned_sl_price_dec),
                        tick_tolerance=tick_tol,
                        net_pnl=float(net_pnl),
                        bybit_close_type=bybit_exit_type,
                    )
                elif exit_type is None:
                    exit_type = bybit_exit_type

        if exit_type == "TakeProfit" and float(net_pnl) < 0:
            corrected = "StopLoss" if exit_at_level else "Manual"
            logger.warning(
                f"{log_prefix} SANITY: TakeProfit with negative net_pnl={float(net_pnl):.6f} "
                f"(exit={float(avg_exit_price)}, at_level={exit_at_level}) — correcting to {corrected}"
            )
            exit_type = corrected

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

    try:
        client = bigquery_logger.get_bigquery_client()
        errors = client.insert_rows_json(bigquery_logger.REAL_TRADES_TABLE_REF, [transformed_data])

        if not errors:
            state_manager.mark_closed_pnl_record_logged(order_id)
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