# bot_service/msi_order_handler.py
# Egzekucja Limit GTC + SL dla sygnałów MSI OrderBlock (demo Bybit).

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, Optional

from shared_lib.models import AlertData
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size, round_qty_by_step
from shared_lib.ob_execution import setup_from_levels, validate_ob_sanity

from bot_service import state_manager
from bot_service.bot_logic import (
    _build_signal_analysis_data,
    _ensure_sl_valid_for_bybit,
    _mark_price_for_symbol,
    _price_api_str,
    _submit_signal_analytics,
    async_call_with_retry,
    log_struct,
    round_price_by_tick,
)
from bot_service.bybit_executor import BybitExecutor

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).upper().replace(".P", "")


async def _cancel_superseded_msi_limits(
    executor: BybitExecutor,
    symbol: str,
    new_event_id: str,
) -> int:
    """
    Nowy OB → anuluj poprzednie niewypełnione limity MSI dla tego symbolu.
    Pozycje OPEN (wypełnione) nie są dotykane.
    """
    cancelled = 0
    sym = _normalize_symbol(symbol)
    pending = state_manager.get_pending_msi_limit_orders(sym)
    for doc in pending:
        old_event_id = doc.id
        if old_event_id == new_event_id:
            continue
        data = doc.to_dict() or {}
        old_symbol = data.get("symbol") or symbol
        try:
            ok = await asyncio.to_thread(
                executor.cancel_order_by_link_id,
                old_symbol,
                old_event_id,
            )
            if ok:
                state_manager.update_active_order(old_event_id, {
                    "status": "CANCELLED_SUPERSEDED",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                    "superseded_by": new_event_id,
                })
                cancelled += 1
                log_struct(
                    "info",
                    "msi_cancel",
                    "Anulowano stary limit MSI (nowy OB)",
                    symbol=sym,
                    event_id=old_event_id,
                    superseded_by=new_event_id,
                )
            else:
                log_struct(
                    "warning",
                    "msi_cancel",
                    "Nie udało się anulować starego limitu MSI",
                    symbol=sym,
                    event_id=old_event_id,
                )
        except Exception as e:
            log_struct(
                "error",
                "msi_cancel",
                f"Błąd anulowania starego limitu: {e}",
                symbol=sym,
                event_id=old_event_id,
            )
    return cancelled


async def handle_msi_ob_limit_signal(
    payload: Dict[str, Any],
    executor: BybitExecutor,
) -> None:
    """Limit GTC + stopLoss, bez fixed TP. Drugi sanity check przed place_order."""
    start_total = perf_counter()
    event_id = str(payload.get("event_id", "unknown"))
    symbol = _normalize_symbol(payload.get("symbol", "unknown"))

    try:
        signal = AlertData.model_validate(payload)
    except Exception as e:
        logger.error(f"[{event_id}] msi_alert_validation: Błąd walidacji symbol={symbol} error={e}")
        return

    raw = signal.raw_context if isinstance(signal.raw_context, dict) else {}
    chain_id = str(raw.get("chain_id", event_id))

    rules_map = await asyncio.to_thread(get_instrument_rules)
    rules = None
    for key in (symbol, f"{symbol}.P"):
        rules = rules_map.get(key)
        if rules:
            break
    if not rules:
        logger.error(f"[{event_id}] msi_signal: REJECT — brak instrument_rules symbol={symbol}")
        return

    tick_size = str(rules.get("tickSize"))
    qty_step = str(rules.get("qtyStep"))
    is_long = signal.direction == "LONG"

    f_entry = round_price_by_tick(signal.entry, tick_size, "down" if is_long else "up")
    f_sl = round_price_by_tick(signal.sl, tick_size, "up" if is_long else "down")

    setup = setup_from_levels(
        signal.direction,
        f_entry,
        f_sl,
        chain_id=chain_id,
        symbol=symbol,
        ob_high=raw.get("ob_high"),
        ob_low=raw.get("ob_low"),
    )
    ok, reason = validate_ob_sanity(setup)
    if not ok:
        log_struct(
            "error",
            "msi_sanity",
            f"REJECT — drugi sanity check: {reason}",
            symbol=symbol,
            event_id=event_id,
            chain_id=chain_id,
            entry=f_entry,
            sl=f_sl,
        )
        return

    if f_sl == f_entry:
        log_struct(
            "warning",
            "msi_sanity",
            "REJECT — f_sl == f_entry po zaokrągleniu tick",
            symbol=symbol,
            event_id=event_id,
            entry=f_entry,
            sl=f_sl,
        )
        return

    # Anuluj stare niewypełnione limity (OPEN pozostają — trailing)
    n_cancelled = await _cancel_superseded_msi_limits(executor, symbol, event_id)
    if n_cancelled:
        log_struct(
            "info",
            "msi_cancel",
            f"Anulowano {n_cancelled} poprzedni(ych) limit(ów) MSI",
            symbol=symbol,
            event_id=event_id,
        )

    try:
        calculated_qty = round_qty_by_step(
            calculate_position_size(
                risk_per_trade_usdt=signal.risk_usdt,
                entry_price=f_entry,
                sl_price=f_sl,
                qty_step=qty_step,
            ),
            qty_step,
        )
        if calculated_qty <= 0:
            logger.error(f"[{event_id}] msi_signal: REJECT — qty=0 symbol={symbol}")
            return

        sl_distance = abs(f_entry - f_sl)
        planned_risk_usdt = calculated_qty * sl_distance
        if planned_risk_usdt > 2.5:
            log_struct(
                "warning",
                "msi_signal",
                f"REJECT — planned_risk {planned_risk_usdt:.3f} USDT > 2.5",
                symbol=symbol,
                event_id=event_id,
            )
            return

        if is_long:
            planned_2r_price = round_price_by_tick(f_entry + 2 * sl_distance, tick_size, "up")
        else:
            planned_2r_price = round_price_by_tick(f_entry - 2 * sl_distance, tick_size, "down")

        mark_price = await asyncio.to_thread(_mark_price_for_symbol, executor, symbol)
        adjusted_sl = _ensure_sl_valid_for_bybit(
            f_sl, f_entry, is_long, tick_size, mark_price, event_id, symbol,
        )
        if adjusted_sl is None:
            return
        if adjusted_sl != f_sl:
            f_sl = adjusted_sl
            setup = setup_from_levels(
                signal.direction, f_entry, f_sl,
                chain_id=chain_id, symbol=symbol,
                ob_high=raw.get("ob_high"), ob_low=raw.get("ob_low"),
            )
            ok, reason = validate_ob_sanity(setup)
            if not ok:
                log_struct(
                    "error",
                    "msi_sanity",
                    f"REJECT po korekcie SL: {reason}",
                    symbol=symbol,
                    event_id=event_id,
                )
                return
            sl_distance = abs(f_entry - f_sl)
            planned_risk_usdt = calculated_qty * sl_distance
            if planned_risk_usdt > 2.5:
                log_struct(
                    "warning",
                    "msi_signal",
                    f"REJECT — planned_risk {planned_risk_usdt:.3f} po korekcie SL",
                    symbol=symbol,
                    event_id=event_id,
                )
                return
            if is_long:
                planned_2r_price = round_price_by_tick(f_entry + 2 * sl_distance, tick_size, "up")
            else:
                planned_2r_price = round_price_by_tick(f_entry - 2 * sl_distance, tick_size, "down")

    except Exception as e:
        logger.error(f"[{event_id}] msi_signal: błąd obliczeń qty symbol={symbol} error={e}")
        return

    order_params = {
        "symbol": symbol,
        "side": "Buy" if is_long else "Sell",
        "orderType": "Limit",
        "qty": str(calculated_qty),
        "price": _price_api_str(f_entry),
        "stopLoss": _price_api_str(f_sl),
        "timeInForce": "GTC",
        "orderLinkId": event_id,
    }

    state_payload = {
        "symbol": symbol,
        "status": "PLACING",
        "direction": signal.direction,
        "planned_qty": calculated_qty,
        "params": order_params,
        "event_id": event_id,
        "signal_id": signal.signal_id,
        "timestamp_signal": signal.timestamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "planned_entry_price": f_entry,
        "planned_sl_price": f_sl,
        "planned_tp_price": planned_2r_price,
        "planned_2r_price": planned_2r_price,
        "session": signal.session,
        "tick_size": tick_size,
        "signal_mode": "msi_orderblock",
        "order_kind": "msi_limit",
        "chain_id": chain_id,
        "risk_ob": setup.risk_ob,
    }

    await asyncio.to_thread(state_manager.save_active_order_transactional, event_id, state_payload)
    log_struct(
        "info",
        "msi_place",
        "Zapisano active_order PLACING (Limit GTC)",
        symbol=symbol,
        event_id=event_id,
        chain_id=chain_id,
        entry=f_entry,
        sl=f_sl,
    )

    analysis_data = _build_signal_analysis_data(
        signal, event_id, symbol, f_entry, f_sl, planned_2r_price, calculated_qty, sl_distance,
    )
    analysis_data["signal_mode"] = "msi_orderblock"
    analysis_data["chain_id"] = chain_id
    _submit_signal_analytics(analysis_data, event_id)

    try:
        async def _place() -> Optional[Dict[str, str]]:
            return await executor.place_order(
                symbol=symbol,
                side="Buy" if is_long else "Sell",
                qty=calculated_qty,
                order_type="Limit",
                price=f_entry,
                stop_loss=f_sl,
                time_in_force="GTC",
                event_id=event_id,
            )

        order_result = await async_call_with_retry(_place, event_id=event_id)
        log_struct(
            "info",
            "msi_place",
            f"place_order Limit result: {order_result}",
            symbol=symbol,
            event_id=event_id,
        )

        if order_result and order_result.get("orderId"):
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "PLACED", "orderId": order_result.get("orderId")},
            )
            log_struct(
                "info",
                "msi_place",
                "Limit GTC + SL wysłany na Bybit demo",
                symbol=symbol,
                event_id=event_id,
                order_id=order_result.get("orderId"),
                entry=f_entry,
                sl=f_sl,
                qty=calculated_qty,
            )
        else:
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "PLACEMENT_FAILED"},
            )
            log_struct(
                "error",
                "msi_place",
                "Bybit nie zwrócił orderId dla Limit GTC",
                symbol=symbol,
                event_id=event_id,
            )

    except Exception as e:
        await asyncio.to_thread(
            state_manager.update_active_order,
            event_id,
            {"status": "ERROR", "error": str(e)},
        )
        log_struct(
            "error",
            "msi_place",
            f"Krytyczny błąd egzekucji Limit: {e}",
            symbol=symbol,
            event_id=event_id,
        )
        return

    elapsed_ms = (perf_counter() - start_total) * 1000.0
    log_struct(
        "info",
        "msi_place",
        f"handle_msi_ob_limit_signal zakończone w {elapsed_ms:.1f} ms",
        symbol=symbol,
        event_id=event_id,
    )
