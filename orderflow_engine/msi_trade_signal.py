# orderflow_engine/msi_trade_signal.py
# OB_NEW → alert Limit GTC do bot_service (egzekucja MSI OrderBlock).

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orderflow_engine.bot_sender import send_alert_to_bot
from orderflow_engine.msi_engine import OrderBlock, StructureEvent
from orderflow_engine.ob_orderflow_snapshot import collect_ob_orderflow_features
from orderflow_engine.settings import get_trading_session
from shared_lib.ob_execution import build_ob_trade_setup
from shared_lib.signal_mode import msi_trade_enabled

logger = logging.getLogger(__name__)

MSI_TRADE_ENABLED = msi_trade_enabled()
DEFAULT_RISK_USDT = float(os.environ.get("MSI_RISK_USDT", "2.5"))


def msi_order_event_id(chain_id: str) -> str:
    """Bybit orderLinkId max 36 znaków."""
    cid = str(chain_id).strip()
    return cid if len(cid) <= 36 else cid[:36]


def build_msi_ob_alert(
    ob: OrderBlock,
    event: StructureEvent,
    processor: Any,
) -> Optional[Dict[str, Any]]:
    """Buduje payload alertu dla bot_service z OB + filtr entry/SL."""
    sym = str(event.symbol).upper().replace(".P", "")
    setup = build_ob_trade_setup(ob, symbol=sym, log_prefix="[MSI-TRADE]")
    if setup is None:
        return None

    event_id = msi_order_event_id(ob.chain_id)
    now = datetime.now(timezone.utc)
    session = get_trading_session(now)
    sl_distance = setup.risk_ob
    if setup.direction == "LONG":
        planned_2r = setup.entry_limit + 2 * sl_distance
    else:
        planned_2r = setup.entry_limit - 2 * sl_distance

    market_features: Dict[str, Any] = collect_ob_orderflow_features(processor, sym, setup.direction)

    risk_pct = (sl_distance / setup.entry_limit * 100.0) if setup.entry_limit > 0 else 0.0

    return {
        "event_id": event_id,
        "signal_id": f"MSI-OB-{sym}-{ob.detected_at_ts}",
        "symbol": sym,
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "direction": setup.direction,
        "entry": setup.entry_limit,
        "sl": setup.sl,
        "tp": planned_2r,
        "risk_pct": risk_pct,
        "rr": 2.0,
        "risk_usdt": DEFAULT_RISK_USDT,
        "structure_state": 1 if setup.direction == "LONG" else -1,
        "session": session,
        "minute_of_day": now.hour * 60 + now.minute,
        "day_of_week": now.weekday(),
        "signal_mode": "msi_orderblock",
        "order_type": "Limit",
        "time_in_force": "GTC",
        "market_features": market_features,
        "raw_context": {
            "chain_id": ob.chain_id,
            "ob_high": setup.ob_high,
            "ob_low": setup.ob_low,
            "ob_height": setup.ob_height,
            "risk_ob": setup.risk_ob,
            "hl_lh_level": ob.hl_lh_level,
            "bos_level": ob.bos_level,
            "liquidity_level": ob.liquidity_level,
            "initial_trend": ob.initial_trend,
            "ob_formed_ts": ob.detected_at_ts,
            "is_first_ob": ob.is_first,
            "msi_event_ts": event.ts,
        },
    }


async def send_msi_ob_alert(
    ob: OrderBlock,
    event: StructureEvent,
    processor: Any,
) -> bool:
    if not MSI_TRADE_ENABLED:
        logger.debug("[MSI-TRADE] MSI_TRADE_ENABLED=false — pomijam alert OB")
        return False

    alert = build_msi_ob_alert(ob, event, processor)
    if alert is None:
        return False

    sym = alert["symbol"]
    logger.info(
        "[MSI-TRADE] Wysyłam OB limit alert %s %s chain=%s entry=%s sl=%s event_id=%s",
        sym,
        alert["direction"],
        ob.chain_id,
        alert["entry"],
        alert["sl"],
        alert["event_id"],
    )
    ok = await send_alert_to_bot(alert)
    if ok:
        logger.info("[MSI-TRADE] Alert dostarczony do bot_service symbol=%s event_id=%s", sym, alert["event_id"])
    else:
        logger.error("[MSI-TRADE] Alert NIE dostarczony symbol=%s event_id=%s", sym, alert["event_id"])
    return ok
