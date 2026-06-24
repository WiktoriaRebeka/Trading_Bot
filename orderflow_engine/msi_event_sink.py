# orderflow_engine/msi_event_sink.py
# Łączy logowanie MSI (BQ) z wysyłką alertów handlowych OB_NEW.

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from orderflow_engine.msi_engine import OrderBlock, StructureEvent
from orderflow_engine.msi_event_logger import MsiEventLogger
from orderflow_engine.msi_trade_signal import (
    MSI_TRADE_ENABLED,
    build_msi_ob_alert,
    send_msi_ob_alert,
)

logger = logging.getLogger(__name__)


class MsiEventSink:
    """Sink dla MsiEngine: log + opcjonalnie trade alert na OB_NEW."""

    def __init__(self, msi_logger: MsiEventLogger, processor):
        self._logger = msi_logger
        self._processor = processor

    def handle_event(self, event: StructureEvent, ob: Optional[OrderBlock] = None) -> None:
        """Spójny interfejs z MsiEventLogger — używany przez MsiEngine.on_event."""
        try:
            self._logger.handle_event(event, ob)
        except Exception as e:
            logger.error("[MSI] logger.handle_event failed: %s", e, exc_info=True)

        if event.event_type == "OB_NEW" and ob is not None:
            if MSI_TRADE_ENABLED:
                self._schedule_trade_alert(ob, event)
            else:
                self._log_dry_run_trade_setup(ob, event)

    def __call__(self, event: StructureEvent, ob: Optional[OrderBlock] = None) -> None:
        self.handle_event(event, ob)

    def _log_dry_run_trade_setup(self, ob: OrderBlock, event: StructureEvent) -> None:
        """Etap 1: geometria entry/SL w logach bez wysyłki do bot_service."""
        alert = build_msi_ob_alert(ob, event, self._processor)
        if alert is None:
            logger.info(
                "[MSI-TRADE-DRY] OB odrzucony (filtr/sanity) symbol=%s chain=%s",
                event.symbol,
                ob.chain_id,
            )
            return
        logger.info(
            "[MSI-TRADE-DRY] symbol=%s %s chain=%s entry=%s sl=%s risk_ob=%s "
            "(SIGNAL_MODE=footprint — bez zlecenia)",
            alert["symbol"],
            alert["direction"],
            ob.chain_id,
            alert["entry"],
            alert["sl"],
            alert.get("raw_context", {}).get("risk_ob"),
        )

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
