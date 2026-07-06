# bot_service/orderblock_bq_logger.py
# Log ORDER_PLACED + TRADE_OUTCOME do orderblock_events (link po chain_id).

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from shared_lib import constants
from shared_lib.orderblock_bq import (
    coerce_raw_context_dict,
    insert_orderblock_rows,
    outcome_from_net_pnl,
    round_bq_float,
    sanitize_orderblock_row,
)

logger = logging.getLogger(__name__)

_bq_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ob-bq")


def _table_id() -> str:
    return f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.orderblock_events"


def _submit_row(row: dict) -> None:
    from bot_service import bigquery_logger

    if not bigquery_logger.initialize_bigquery():
        logger.warning("[OB-BQ] BigQuery niedostępne — pomijam insert event_type=%s", row.get("event_type"))
        return
    client = bigquery_logger.get_bigquery_client()
    table = _table_id()
    sanitized = sanitize_orderblock_row(row)
    _bq_executor.submit(_do_insert, client, table, sanitized)


def _do_insert(client: Any, table_id: str, row: dict) -> None:
    rc = row.get("raw_context")
    if rc is not None and not isinstance(rc, dict):
        logger.error(
            "[OB-BQ] raw_context musi być dict (otrzymano %s) — pomijam insert chain=%s type=%s",
            type(rc).__name__,
            row.get("chain_id"),
            row.get("event_type"),
        )
        return
    try:
        errors = insert_orderblock_rows(client, table_id, [row])
        if errors:
            logger.error("[OB-BQ] insert errors chain=%s type=%s: %s", row.get("chain_id"), row.get("event_type"), errors)
        else:
            logger.info(
                "[OB-BQ] OK event_type=%s chain=%s symbol=%s",
                row.get("event_type"),
                row.get("chain_id"),
                row.get("symbol"),
            )
    except Exception as e:
        logger.error("[OB-BQ] insert failed: %s", e, exc_info=True)


def log_rejected_failed_break(
    *,
    chain_id: str,
    symbol: str,
    direction: str,
    entry_limit: float,
    sl: float,
    risk_ob: float,
    event_id: str,
    timestamp_signal: str,
    session: Optional[str] = None,
    cooldown_minutes: int = 30,
    min_low: Optional[float] = None,
    max_high: Optional[float] = None,
    market_features: Optional[Dict[str, Any]] = None,
) -> None:
    """REJECTED_FAILED_BREAK — cena dotknęła entry_limit w oknie karencji (failed break)."""
    now = datetime.now(timezone.utc)
    mf = market_features or {}
    row = {
        "event_id": str(uuid.uuid4()),
        "event_type": "REJECTED_FAILED_BREAK",
        "symbol": str(symbol).upper().replace(".P", ""),
        "event_ts": now,
        "session": session,
        "minute_of_day": now.hour * 60 + now.minute,
        "day_of_week": now.weekday(),
        "ob_id": chain_id,
        "chain_id": chain_id,
        "ob_direction": str(direction).upper(),
        "entry_limit": entry_limit,
        "sl": sl,
        "risk_ob": risk_ob,
        "outcome": "REJECTED_FAILED_BREAK",
        "matched_liq_volume": mf.get("matched_liq_volume"),
        "obi": mf.get("obi") or mf.get("obi_value"),
        "funding_rate": mf.get("funding_rate"),
        "dom_wall": mf.get("dom_wall") if mf.get("dom_wall") is not None else mf.get("real_wall_detected"),
        "delta": mf.get("delta") or mf.get("delta_last"),
        "trade_event_id": event_id,
        "raw_context": coerce_raw_context_dict({
            "source": "bot_service",
            "trade_event_id": event_id,
            "timestamp_signal": timestamp_signal,
            "cooldown_minutes": cooldown_minutes,
            "min_low_in_window": min_low,
            "max_high_in_window": max_high,
            "reject_reason": "failed_break_entry_touched",
        }),
    }
    _submit_row(row)


def log_order_placed(
    *,
    chain_id: str,
    symbol: str,
    direction: str,
    entry_limit: float,
    sl: float,
    risk_ob: float,
    event_id: str,
    order_id: Optional[str] = None,
    session: Optional[str] = None,
    market_features: Optional[Dict[str, Any]] = None,
    tp: Optional[float] = None,
) -> None:
    """ORDER_PLACED — faktyczne poziomy po tick rounding (bot_service)."""
    now = datetime.now(timezone.utc)
    mf = market_features or {}
    row = {
        "event_id": str(uuid.uuid4()),
        "event_type": "ORDER_PLACED",
        "symbol": str(symbol).upper().replace(".P", ""),
        "event_ts": now,
        "session": session,
        "minute_of_day": now.hour * 60 + now.minute,
        "day_of_week": now.weekday(),
        "ob_id": chain_id,
        "chain_id": chain_id,
        "ob_direction": str(direction).upper(),
        "entry_limit": entry_limit,
        "sl": sl,
        "risk_ob": risk_ob,
        "matched_liq_volume": mf.get("matched_liq_volume"),
        "obi": mf.get("obi") or mf.get("obi_value"),
        "funding_rate": mf.get("funding_rate"),
        "dom_wall": mf.get("dom_wall") if mf.get("dom_wall") is not None else mf.get("real_wall_detected"),
        "delta": mf.get("delta") or mf.get("delta_last"),
        "trade_event_id": event_id,
        "trade_order_id": order_id,
        "raw_context": coerce_raw_context_dict({
            "source": "bot_service",
            "trade_event_id": event_id,
            "order_id": order_id,
            "planned_tp_price": tp,
        }),
    }
    _submit_row(row)


def log_trade_outcome(
    *,
    chain_id: str,
    symbol: str,
    direction: str,
    trade_event_id: str,
    trade_order_id: str,
    outcome: str,
    realized_r: Optional[float],
    net_pnl_usdt: Optional[float],
    avg_entry_price: Optional[float] = None,
    avg_exit_price: Optional[float] = None,
    exit_type: Optional[str] = None,
    risk_ob: Optional[float] = None,
    session: Optional[str] = None,
    planned_tp_price: Optional[float] = None,
) -> None:
    """TRADE_OUTCOME — link do OB po chain_id (wywołane z pnl_logger_real)."""
    now = datetime.now(timezone.utc)
    if not outcome and net_pnl_usdt is not None:
        outcome = outcome_from_net_pnl(net_pnl_usdt) or "UNKNOWN"

    row = {
        "event_id": str(uuid.uuid4()),
        "event_type": "TRADE_OUTCOME",
        "symbol": str(symbol).upper().replace(".P", ""),
        "event_ts": now,
        "session": session,
        "minute_of_day": now.hour * 60 + now.minute,
        "day_of_week": now.weekday(),
        "ob_id": chain_id,
        "chain_id": chain_id,
        "ob_direction": str(direction).upper(),
        "outcome": outcome,
        "realized_r": round_bq_float(realized_r),
        "risk_ob": round_bq_float(risk_ob),
        "trade_event_id": trade_event_id,
        "trade_order_id": trade_order_id,
        "raw_context": coerce_raw_context_dict({
            "source": "pnl_logger_real",
            "trade_event_id": trade_event_id,
            "trade_order_id": trade_order_id,
            "exit_type": exit_type,
            "net_pnl_usdt": round_bq_float(net_pnl_usdt),
            "avg_entry_price": round_bq_float(avg_entry_price),
            "avg_exit_price": round_bq_float(avg_exit_price),
            "planned_tp_price": round_bq_float(planned_tp_price),
        }),
    }
    _submit_row(row)


def compute_msi_realized_r(
    direction: str,
    avg_entry_price: float,
    avg_exit_price: float,
    risk_ob: float,
) -> Optional[float]:
    """R-multiple względem wysokości strefy OB (1R = risk_ob)."""
    if risk_ob <= 0:
        return None
    side = str(direction).upper()
    if side == "LONG":
        return (avg_exit_price - avg_entry_price) / risk_ob
    if side == "SHORT":
        return (avg_entry_price - avg_exit_price) / risk_ob
    return None
