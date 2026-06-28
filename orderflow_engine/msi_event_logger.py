# orderflow_engine/msi_event_logger.py
# Logowanie eventów MSI (OB + struktura) do BigQuery orderblock_events.

from __future__ import annotations

import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from google.cloud import bigquery

from orderflow_engine.msi_engine import OrderBlock, StructureEvent
from orderflow_engine.ob_orderflow_snapshot import collect_ob_orderflow_features
from orderflow_engine.settings import get_trading_session
from shared_lib import constants
from shared_lib.ob_execution import build_ob_trade_setup
from shared_lib.orderblock_bq import (
    coerce_raw_context_dict,
    insert_orderblock_rows,
    sanitize_orderblock_row,
)

logger = logging.getLogger(__name__)

_bq_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="msi-bq")

# Mapowanie typów eventów MSI → nazwy w BQ
_EVENT_TYPE_ALIASES = {
    "LIQUIDITY": "LIQUIDITY_GRAB",
}


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _ts_ms_to_datetime(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


class MsiEventLogger:
    """Zapisuje eventy MSI do logów i BigQuery orderblock_events."""

    def __init__(self, project_id: Optional[str] = None, processor: Any = None):
        self._processor = processor
        self.log_to_bq = os.environ.get("MSI_LOG_TO_BQ", "true").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.log_file = os.environ.get("MSI_LOG_FILE", "").strip()
        self._bq_client = None
        if self.log_to_bq:
            pid = project_id or constants.BIGQUERY_PROJECT_ID
            try:
                self._bq_client = bigquery.Client(project=pid)
                logger.info("[MSI] BigQuery logger ready project=%s", pid)
            except Exception as e:
                logger.error("[MSI] BigQuery init failed: %s — file/log only", e)
                self.log_to_bq = False

    def handle_event(self, event: StructureEvent, ob: Optional[OrderBlock] = None) -> None:
        if event.event_type == "TEMP_UPDATE":
            return  # wyłączone — telemetria co świeczkę 1M, zaśmieca orderblock_events
        if event.event_type == "OB_NEW" and ob is not None:
            self._log_ob_detected(ob, event)
        else:
            self._log_structure_event(event)

    def _bq_event_type(self, event_type: str) -> str:
        return _EVENT_TYPE_ALIASES.get(event_type, event_type)

    def _log_structure_event(self, event: StructureEvent) -> None:
        bq_type = self._bq_event_type(event.event_type)
        msg = (
            f"[MSI][{bq_type}] symbol={event.symbol} "
            f"ts={_ts_ms_to_datetime(event.ts).isoformat()} "
            f"payload={json.dumps(event.payload, default=str)}"
        )
        logger.debug(msg)
        self._persist_row(self._build_row(event, None, bq_type))

    def _log_ob_detected(self, ob: OrderBlock, event: StructureEvent) -> None:
        dt = _ts_ms_to_datetime(ob.detected_at_ts)
        session = get_trading_session(dt)
        sym = str(event.symbol).upper()
        setup = build_ob_trade_setup(ob, symbol=sym, log_prefix="[MSI-BQ]")
        extra = ""
        if setup is not None:
            extra = (
                f" entry_limit={setup.entry_limit} sl={setup.sl} "
                f"risk_ob={setup.risk_ob:.8f} height_pct={setup.ob_height_pct:.4f}%"
            )
        msg = (
            f"[MSI][OB_DETECTED] symbol={event.symbol} "
            f"ts={dt.isoformat()} direction={ob.direction} "
            f"ob_high={ob.ob_high} ob_low={ob.ob_low} ob_height={ob.ob_height:.8f} "
            f"chain_id={ob.chain_id} initial_trend={ob.initial_trend} "
            f"hl_lh_level={ob.hl_lh_level} bos_level={ob.bos_level} "
            f"liquidity_level={ob.liquidity_level} session={session} "
            f"is_first={ob.is_first}{extra}"
        )
        logger.info(msg)
        self._persist_row(self._build_row(event, ob, "OB_NEW"))

    def _build_row(
        self,
        event: StructureEvent,
        ob: Optional[OrderBlock],
        event_type: str,
    ) -> dict:
        dt = _ts_ms_to_datetime(event.ts)
        session = get_trading_session(dt)
        sym = str(event.symbol).upper().replace(".P", "")

        row: dict = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "symbol": sym,
            "event_ts": dt,
            "session": session,
            "minute_of_day": dt.hour * 60 + dt.minute,
            "day_of_week": dt.weekday(),
            "raw_context": coerce_raw_context_dict(event.payload),
        }

        if ob is not None:
            setup = build_ob_trade_setup(ob, symbol=sym, log_prefix="[MSI-BQ]")
            row.update(self._ob_fields(ob, setup))
            row.update(
                collect_ob_orderflow_features(self._processor, sym, ob.direction)
            )
        elif event_type == "OB_NEW":
            p = event.payload or {}
            row.update({
                "ob_id": p.get("chain_id"),
                "chain_id": p.get("chain_id"),
                "ob_formed_ts": dt,
                "ob_candle_ts": _ts_ms_to_datetime(int(p.get("ob_candle_ts", event.ts))),
                "ob_high": p.get("ob_high"),
                "ob_low": p.get("ob_low"),
                "ob_height": p.get("ob_height"),
                "ob_direction": p.get("ob_direction"),
                "initial_trend": p.get("initial_trend"),
                "hl_lh_level": p.get("hl_lh_level"),
                "bos_level": p.get("bos_level"),
                "liquidity_level": p.get("liquidity_level"),
                "is_first_ob": p.get("is_first"),
            })
            direction = p.get("ob_direction") or "LONG"
            row.update(
                collect_ob_orderflow_features(self._processor, sym, str(direction))
            )
            if p.get("ob_high") is not None and p.get("ob_low") is not None:
                try:
                    hi, lo = float(p["ob_high"]), float(p["ob_low"])
                    direction_u = str(direction).upper()
                    if direction_u == "LONG":
                        row["entry_limit"] = hi
                        row["sl"] = lo
                    else:
                        row["entry_limit"] = lo
                        row["sl"] = hi
                    row["risk_ob"] = hi - lo
                except (TypeError, ValueError):
                    pass
        elif event.payload and event.payload.get("chain_id"):
            row["chain_id"] = event.payload.get("chain_id")
            row["ob_id"] = event.payload.get("chain_id")

        return row

    @staticmethod
    def _ob_fields(ob: OrderBlock, setup: Any) -> dict:
        fields = {
            "ob_id": ob.chain_id,
            "chain_id": ob.chain_id,
            "ob_formed_ts": _ts_ms_to_datetime(ob.detected_at_ts),
            "ob_candle_ts": _ts_ms_to_datetime(ob.candle.ts),
            "ob_high": ob.ob_high,
            "ob_low": ob.ob_low,
            "ob_height": ob.ob_height,
            "ob_direction": ob.direction,
            "initial_trend": ob.initial_trend,
            "hl_lh_level": ob.hl_lh_level,
            "bos_level": ob.bos_level,
            "liquidity_level": ob.liquidity_level,
            "is_first_ob": ob.is_first,
        }
        if setup is not None:
            fields["entry_limit"] = setup.entry_limit
            fields["sl"] = setup.sl
            fields["risk_ob"] = setup.risk_ob
        return fields

    def _persist_row(self, row: dict) -> None:
        sanitized = sanitize_orderblock_row(row)

        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(sanitized, default=_json_default) + "\n")
            except Exception as e:
                logger.warning("[MSI] file log failed: %s", e)

        if not self.log_to_bq or self._bq_client is None:
            return

        table_id = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.orderblock_events"
        _bq_executor.submit(self._do_insert, table_id, sanitized)

    def _do_insert(self, table_id: str, row: dict) -> None:
        if self._bq_client is None:
            return
        rc = row.get("raw_context")
        if rc is not None and not isinstance(rc, dict):
            logger.error(
                "[MSI][BQ] raw_context musi być dict (otrzymano %s) — pomijam insert",
                type(rc).__name__,
            )
            return
        try:
            errors = insert_orderblock_rows(self._bq_client, table_id, [row])
            if errors:
                logger.error("[MSI][BQ] insert errors: %s", errors)
        except Exception as e:
            logger.error("[MSI][BQ] insert failed: %s", e)
