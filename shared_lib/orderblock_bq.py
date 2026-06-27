# shared_lib/orderblock_bq.py
# Sanitizacja wierszy orderblock_events (BigQuery NUMERIC max 8 miejsc po przecinku).

from __future__ import annotations

import json
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BQ_NUMERIC_PRECISION = 8

ORDERBLOCK_EVENTS_COLUMNS = (
    "event_id",
    "event_type",
    "symbol",
    "event_ts",
    "session",
    "minute_of_day",
    "day_of_week",
    "ob_id",
    "chain_id",
    "ob_formed_ts",
    "ob_candle_ts",
    "ob_high",
    "ob_low",
    "ob_height",
    "ob_direction",
    "initial_trend",
    "hl_lh_level",
    "bos_level",
    "liquidity_level",
    "is_first_ob",
    "entry_limit",
    "sl",
    "risk_ob",
    "matched_liq_volume",
    "obi",
    "funding_rate",
    "dom_wall",
    "delta",
    "outcome",
    "realized_r",
    "trade_order_id",
    "trade_event_id",
    "raw_context",
)


def round_bq_float(value: Any, ndigits: int = BQ_NUMERIC_PRECISION) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return round(value, ndigits)
    return value


def round_bq_numerics(value: Any, ndigits: int = BQ_NUMERIC_PRECISION) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round_bq_float(value, ndigits)
    if isinstance(value, dict):
        return {str(k): round_bq_numerics(v, ndigits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_bq_numerics(v, ndigits) for v in value]
    return value


def normalize_bq_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (int, float)):
        try:
            ms = float(value)
            if ms < 10**12:
                ms *= 1000.0
            return normalize_bq_timestamp(datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _coerce_json_native(value: Any) -> Any:
    """Wartości zgodne z insert_rows_json do kolumny BigQuery JSON."""
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _coerce_json_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_json_native(v) for v in value]
    return str(value)


def coerce_raw_context_dict(value: Any) -> Optional[Dict[str, Any]]:
    """
    raw_context dla orderblock_events: dict lub None.
    Akceptuje dict; JSON-string parsuje do dict (nigdy nie zostawia stringa).
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("orderblock_bq: raw_context string nie jest poprawnym JSON — pomijam")
            return None
    if not isinstance(value, dict):
        logger.warning(
            "orderblock_bq: raw_context wymaga dict, otrzymano %s — pomijam",
            type(value).__name__,
        )
        return None
    return {str(k): _coerce_json_native(v) for k, v in value.items()}


def sanitize_raw_context(value: Any) -> Optional[Dict[str, Any]]:
    """raw_context musi być dict lub None — nigdy json.dumps string."""
    coerced = coerce_raw_context_dict(value)
    if coerced is None:
        return None
    return round_bq_numerics(coerced)


def sanitize_orderblock_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in ORDERBLOCK_EVENTS_COLUMNS:
        val = row.get(col)
        if col in ("event_ts", "ob_formed_ts", "ob_candle_ts"):
            out[col] = normalize_bq_timestamp(val)
        elif col == "raw_context":
            out[col] = sanitize_raw_context(val)
        elif col in (
            "minute_of_day",
            "day_of_week",
        ):
            out[col] = int(val) if val is not None else None
        elif col in ("is_first_ob", "dom_wall"):
            out[col] = bool(val) if val is not None else None
        elif col in (
            "ob_high", "ob_low", "ob_height", "hl_lh_level", "bos_level", "liquidity_level",
            "entry_limit", "sl", "risk_ob", "matched_liq_volume", "obi", "funding_rate",
            "delta", "realized_r",
        ):
            out[col] = round_bq_float(val)
        else:
            out[col] = val
    return out


def _json_row_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        dt = obj if obj.tzinfo else obj.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Nieobsługiwany typ JSON: {type(obj)}")


def insert_orderblock_rows(client: Any, table_id: str, rows: List[Dict[str, Any]]) -> List[Any]:
    payload = [sanitize_orderblock_row(r) for r in rows]
    # json round-trip — usuwa typy nieakceptowane przez insert_rows_json (jak w orderflow BQ)
    clean = [json.loads(json.dumps(row, default=_json_row_default)) for row in payload]
    return client.insert_rows_json(table_id, clean)


def outcome_from_net_pnl(net_pnl: Optional[float]) -> Optional[str]:
    if net_pnl is None:
        return None
    try:
        pnl = float(net_pnl)
    except (TypeError, ValueError):
        return None
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BREAKEVEN"
