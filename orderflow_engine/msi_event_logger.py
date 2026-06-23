# orderflow_engine/msi_event_logger.py
# Logowanie eventów MSI (OB + struktura) do BigQuery i logów aplikacji.

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
from orderflow_engine.settings import get_trading_session
from shared_lib import constants

logger = logging.getLogger(__name__)

_bq_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="msi-bq")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _ts_ms_to_datetime(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


class MsiEventLogger:
    """Zapisuje eventy MSI do logów i opcjonalnie do BigQuery orderblock_events."""

    def __init__(self, project_id: Optional[str] = None):
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
        if event.event_type == "OB_NEW" and ob is not None:
            self._log_ob_detected(ob, event)
        else:
            self._log_structure_event(event)

    def _log_structure_event(self, event: StructureEvent) -> None:
        msg = (
            f"[MSI][{event.event_type}] symbol={event.symbol} "
            f"ts={_ts_ms_to_datetime(event.ts).isoformat()} "
            f"payload={json.dumps(event.payload, default=str)}"
        )
        logger.info(msg)
        self._persist_row(self._build_row(event, None))

    def _log_ob_detected(self, ob: OrderBlock, event: StructureEvent) -> None:
        dt = _ts_ms_to_datetime(ob.detected_at_ts)
        session = get_trading_session(dt)
        msg = (
            f"[MSI][OB_DETECTED] symbol={event.symbol} "
            f"ts={dt.isoformat()} direction={ob.direction} "
            f"ob_high={ob.ob_high} ob_low={ob.ob_low} ob_height={ob.ob_height:.8f} "
            f"chain_id={ob.chain_id} initial_trend={ob.initial_trend} "
            f"hl_lh_level={ob.hl_lh_level} bos_level={ob.bos_level} "
            f"liquidity_level={ob.liquidity_level} session={session} "
            f"is_first={ob.is_first}"
        )
        logger.info(msg)
        self._persist_row(self._build_row(event, ob))

    def _build_row(self, event: StructureEvent, ob: Optional[OrderBlock]) -> dict:
        dt = _ts_ms_to_datetime(event.ts)
        session = get_trading_session(dt)
        row: dict = {
            "event_id": str(uuid.uuid4()),
            "event_type": event.event_type,
            "symbol": event.symbol,
            "event_ts": dt,
            "session": session,
            "minute_of_day": dt.hour * 60 + dt.minute,
            "day_of_week": dt.weekday(),
            "raw_context": event.payload,
        }
        if ob is not None:
            row.update(
                {
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
            )
        elif event.event_type == "OB_NEW":
            p = event.payload
            row.update(
                {
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
                }
            )
        return row

    def _persist_row(self, row: dict) -> None:
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, default=_json_default) + "\n")
            except Exception as e:
                logger.warning("[MSI] file log failed: %s", e)

        if not self.log_to_bq or self._bq_client is None:
            return

        table_id = f"{constants.BIGQUERY_DATASET_ID}.orderblock_events"
        _bq_executor.submit(self._do_insert, table_id, row)

    def _do_insert(self, table_id: str, row: dict) -> None:
        if self._bq_client is None:
            return
        try:
            payload = json.loads(json.dumps(row, default=_json_default))
            errors = self._bq_client.insert_rows_json(table_id, [payload])
            if errors:
                logger.error("[MSI][BQ] insert errors: %s", errors)
        except Exception as e:
            logger.error("[MSI][BQ] insert failed: %s", e)
