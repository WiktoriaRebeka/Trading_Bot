# orderflow_engine/msi_event_sink.py
# Łączy logowanie MSI (BQ) z wysyłką alertów handlowych OB_NEW.

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from orderflow_engine.msi_engine import OrderBlock, StructureEvent
from orderflow_engine.msi_event_logger import MsiEventLogger
from orderflow_engine.msi_trade_signal import MSI_TRADE_ENABLED, send_msi_ob_alert

logger = logging.getLogger(__name__)


class MsiEventSink:
    """Sink dla MsiEngine: log + opcjonalnie trade alert na OB_NEW."""

    def __init__(self, msi_logger: MsiEventLogger, processor):
        self._logger = msi_logger
        self._processor = processor

    def __call__(self, event: StructureEvent, ob: Optional[OrderBlock] = None) -> None:
        try:
            self._logger.handle_event(event, ob)
        except Exception as e:
            logger.error("[MSI] logger.handle_event failed: %s", e, exc_info=True)

        if (
            MSI_TRADE_ENABLED
            and event.event_type == "OB_NEW"
            and ob is not None
        ):
            self._schedule_trade_alert(ob, event)

    def _schedule_trade_alert(self, ob: OrderBlock, event: StructureEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_msi_ob_alert(ob, event, self._processor))
        except RuntimeError:
            logger.warning(
                "[MSI-TRADE] Brak running event loop — OB alert nie wysłany symbol=%s chain=%s",
                event.symbol,
                ob.chain_id,
            )
