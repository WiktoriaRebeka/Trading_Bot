# bot_service/msi_order_handler.py
# Egzekucja Limit GTC + SL dla sygnałów MSI OrderBlock (demo Bybit).
# Karencja 30 min: COOLDOWN_PENDING → weryfikacja świec 1M → place lub REJECTED_FAILED_BREAK.

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

from shared_lib.models import AlertData
from shared_lib.firebase_client import get_instrument_rules
from shared_lib.risk_manager import calculate_position_size, round_qty_by_step
from shared_lib.ob_execution import (
    compute_ob_tp,
    setup_from_levels,
    validate_ob_sanity,
)

from bot_service import state_manager
from bot_service.bot_logic import (
    _build_signal_analysis_data,
    _ensure_sl_valid_for_bybit,
    _mark_price_for_symbol,
    _price_api_str,
    _submit_signal_analytics,
    async_call_with_retry,
    call_with_retry,
    log_struct,
    round_price_by_tick,
)
from bot_service.bybit_executor import BybitExecutor
from bot_service.orderblock_bq_logger import log_order_placed, log_rejected_failed_break

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).upper().replace(".P", "")

from bot_service.msi_cooldown_logic import (
    MSI_COOLDOWN_MINUTES,
    cooldown_failed_break,
    parse_signal_ts,
)


@dataclass
class MsiPreparedOrder:
    event_id: str
    symbol: str
    chain_id: str
    signal: AlertData
    tick_size: str
    qty_step: str
    is_long: bool
    f_entry: float
    f_sl: float
    f_tp: float
    risk_ob: float
    calculated_qty: float
    sl_distance: float
    order_params: Dict[str, Any]
    timestamp_signal: str


async def _cancel_superseded_msi_limits(
    executor: BybitExecutor,
    symbol: str,
    new_event_id: str,
) -> int:
    """
    Nowy OB → anuluj poprzednie niewypełnione limity MSI dla tego symbolu.
    COOLDOWN_PENDING: tylko update statusu (brak zlecenia na giełdzie).
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
        status = data.get("status")
        try:
            if status == "COOLDOWN_PENDING":
                state_manager.update_active_order(old_event_id, {
                    "status": "CANCELLED_SUPERSEDED",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                    "superseded_by": new_event_id,
                })
                cancelled += 1
                log_struct(
                    "info",
                    "msi_cancel",
                    "Anulowano karencję MSI (nowy OB)",
                    symbol=sym,
                    event_id=old_event_id,
                    superseded_by=new_event_id,
                )
                continue

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


async def _prepare_msi_order(
    payload: Dict[str, Any],
) -> Optional[MsiPreparedOrder]:
    """Walidacja + wyliczenia qty/SL/TP — wspólne dla karencji i place."""
    event_id = str(payload.get("event_id", "unknown"))
    symbol = _normalize_symbol(payload.get("symbol", "unknown"))

    try:
        signal = AlertData.model_validate(payload)
    except Exception as e:
        logger.error(f"[{event_id}] msi_alert_validation: Błąd walidacji symbol={symbol} error={e}")
        return None

    raw = signal.raw_context if isinstance(signal.raw_context, dict) else {}
    chain_id = str(raw.get("chain_id", event_id))
    timestamp_signal = str(signal.timestamp)

    rules_map = await asyncio.to_thread(get_instrument_rules)
    rules = None
    for key in (symbol, f"{symbol}.P"):
        rules = rules_map.get(key)
        if rules:
            break
    if not rules:
        logger.error(f"[{event_id}] msi_signal: REJECT — brak instrument_rules symbol={symbol}")
        return None

    tick_size = str(rules.get("tickSize"))
    qty_step = str(rules.get("qtyStep"))
    is_long = signal.direction == "LONG"

    f_entry = round_price_by_tick(signal.entry, tick_size, "down" if is_long else "up")
    f_sl = round_price_by_tick(signal.sl, tick_size, "up" if is_long else "down")

    risk_ob = (
        float(raw["ob_high"]) - float(raw["ob_low"])
        if raw.get("ob_high") is not None and raw.get("ob_low") is not None
        else abs(f_entry - f_sl)
    )
    f_tp = round_price_by_tick(
        compute_ob_tp(f_entry, risk_ob, signal.direction),
        tick_size,
        "up" if is_long else "down",
    )

    setup = replace(
        setup_from_levels(
            signal.direction,
            f_entry,
            f_sl,
            chain_id=chain_id,
            symbol=symbol,
            ob_high=raw.get("ob_high"),
            ob_low=raw.get("ob_low"),
        ),
        tp=f_tp,
        risk_ob=risk_ob,
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
            tp=f_tp,
        )
        return None

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
        return None

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
            return None

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
            return None

    except Exception as e:
        logger.error(f"[{event_id}] msi_signal: błąd obliczeń qty symbol={symbol} error={e}")
        return None

    order_params = {
        "symbol": symbol,
        "side": "Buy" if is_long else "Sell",
        "orderType": "Limit",
        "qty": str(calculated_qty),
        "price": _price_api_str(f_entry),
        "stopLoss": _price_api_str(f_sl),
        "takeProfit": _price_api_str(f_tp),
        "timeInForce": "GTC",
        "orderLinkId": event_id,
    }

    return MsiPreparedOrder(
        event_id=event_id,
        symbol=symbol,
        chain_id=chain_id,
        signal=signal,
        tick_size=tick_size,
        qty_step=qty_step,
        is_long=is_long,
        f_entry=f_entry,
        f_sl=f_sl,
        f_tp=f_tp,
        risk_ob=risk_ob,
        calculated_qty=calculated_qty,
        sl_distance=abs(f_entry - f_sl),
        order_params=order_params,
        timestamp_signal=timestamp_signal,
    )


async def _place_msi_limit_gtc(
    executor: BybitExecutor,
    prepared: MsiPreparedOrder,
    *,
    market_features: Optional[Dict[str, Any]] = None,
) -> None:
    """Składa Limit GTC po karencji — timestamp_signal z payloadu, bez now()."""
    event_id = prepared.event_id
    symbol = prepared.symbol
    signal = prepared.signal
    f_entry = prepared.f_entry
    f_sl = prepared.f_sl
    f_tp = prepared.f_tp

    mark_price = await asyncio.to_thread(_mark_price_for_symbol, executor, symbol)
    adjusted_sl = _ensure_sl_valid_for_bybit(
        f_sl, f_entry, prepared.is_long, prepared.tick_size, mark_price, event_id, symbol,
    )
    if adjusted_sl is None:
        await asyncio.to_thread(
            state_manager.update_active_order,
            event_id,
            {"status": "ERROR", "error": "sl_invalid_for_bybit"},
        )
        return
    if adjusted_sl != f_sl:
        f_sl = adjusted_sl
        setup = replace(
            setup_from_levels(
                signal.direction,
                f_entry,
                f_sl,
                chain_id=prepared.chain_id,
                symbol=symbol,
            ),
            tp=f_tp,
            risk_ob=prepared.risk_ob,
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
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "ERROR", "error": reason},
            )
            return
        prepared.sl_distance = abs(f_entry - f_sl)
        planned_risk_usdt = prepared.calculated_qty * prepared.sl_distance
        if planned_risk_usdt > 2.5:
            log_struct(
                "warning",
                "msi_signal",
                f"REJECT — planned_risk {planned_risk_usdt:.3f} po korekcie SL",
                symbol=symbol,
                event_id=event_id,
            )
            await asyncio.to_thread(
                state_manager.update_active_order,
                event_id,
                {"status": "ERROR", "error": "planned_risk_too_high_after_sl_adjust"},
            )
            return
        prepared.order_params["stopLoss"] = _price_api_str(f_sl)

    state_payload = {
        "symbol": symbol,
        "status": "PLACING",
        "direction": signal.direction,
        "planned_qty": prepared.calculated_qty,
        "params": prepared.order_params,
        "event_id": event_id,
        "signal_id": signal.signal_id,
        "timestamp_signal": prepared.timestamp_signal,
        "planned_entry_price": f_entry,
        "planned_sl_price": f_sl,
        "planned_tp_price": f_tp,
        "planned_2r_price": f_tp,
        "session": signal.session,
        "tick_size": prepared.tick_size,
        "signal_mode": "msi_orderblock",
        "order_kind": "msi_limit",
        "chain_id": prepared.chain_id,
        "risk_ob": prepared.risk_ob,
        "cooldown_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    await asyncio.to_thread(state_manager.save_active_order_transactional, event_id, state_payload)

    analysis_data = _build_signal_analysis_data(
        signal, event_id, symbol, f_entry, f_sl, f_tp,
        prepared.calculated_qty, prepared.sl_distance,
    )
    analysis_data["signal_mode"] = "msi_orderblock"
    analysis_data["chain_id"] = prepared.chain_id
    _submit_signal_analytics(analysis_data, event_id)

    try:
        async def _place() -> Optional[Dict[str, str]]:
            return await executor.place_order(
                symbol=symbol,
                side="Buy" if prepared.is_long else "Sell",
                qty=prepared.calculated_qty,
                order_type="Limit",
                price=f_entry,
                stop_loss=f_sl,
                take_profit=f_tp,
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
                "Limit GTC + SL + TP (2R) wysłany na Bybit demo",
                symbol=symbol,
                event_id=event_id,
                order_id=order_result.get("orderId"),
                entry=f_entry,
                sl=f_sl,
                tp=f_tp,
                qty=prepared.calculated_qty,
            )
            mf = market_features if isinstance(market_features, dict) else {}
            if not mf and isinstance(signal.market_features, dict):
                mf = signal.market_features
            log_order_placed(
                chain_id=prepared.chain_id,
                symbol=symbol,
                direction=signal.direction,
                entry_limit=f_entry,
                sl=f_sl,
                tp=f_tp,
                risk_ob=prepared.risk_ob,
                event_id=event_id,
                order_id=order_result.get("orderId"),
                session=signal.session,
                market_features=mf,
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


async def handle_msi_ob_limit_signal(
    payload: Dict[str, Any],
    executor: BybitExecutor,
) -> None:
    """Walidacja → COOLDOWN_PENDING (30 min) → place w cyklu update-orders."""
    start_total = perf_counter()
    event_id = str(payload.get("event_id", "unknown"))
    symbol = _normalize_symbol(payload.get("symbol", "unknown"))

    if state_manager.is_signal_logged(event_id):
        log_struct(
            "info",
            "msi_duplicate",
            "Duplicate alert delivery — signal already logged to BQ, skipping",
            symbol=symbol,
            event_id=event_id,
        )
        return

    existing = await asyncio.to_thread(state_manager.get_active_order_by_id, event_id)
    if existing:
        st = existing.get("status")
        if st in ("COOLDOWN_PENDING", "PLACING", "PLACED", "OPEN"):
            log_struct(
                "info",
                "msi_cooldown",
                f"Sygnał już w obsłudze (status={st}) — idempotent skip",
                symbol=symbol,
                event_id=event_id,
            )
            return

    prepared = await _prepare_msi_order(payload)
    if prepared is None:
        return

    n_cancelled = await _cancel_superseded_msi_limits(executor, symbol, event_id)
    if n_cancelled:
        log_struct(
            "info",
            "msi_cancel",
            f"Anulowano {n_cancelled} poprzedni(ych) limit(ów)/karencji MSI",
            symbol=symbol,
            event_id=event_id,
        )

    signal_ts = parse_signal_ts(prepared.timestamp_signal)
    cooldown_until = signal_ts + timedelta(minutes=MSI_COOLDOWN_MINUTES)
    now = datetime.now(timezone.utc)

    mf = prepared.signal.market_features if isinstance(prepared.signal.market_features, dict) else {}

    state_payload = {
        "symbol": symbol,
        "status": "COOLDOWN_PENDING",
        "direction": prepared.signal.direction,
        "planned_qty": prepared.calculated_qty,
        "params": prepared.order_params,
        "event_id": event_id,
        "signal_id": prepared.signal.signal_id,
        "timestamp_signal": prepared.timestamp_signal,
        "created_at": now.isoformat(),
        "cooldown_started_at": now.isoformat(),
        "cooldown_until_at": cooldown_until.isoformat(),
        "cooldown_minutes": MSI_COOLDOWN_MINUTES,
        "planned_entry_price": prepared.f_entry,
        "planned_sl_price": prepared.f_sl,
        "planned_tp_price": prepared.f_tp,
        "planned_2r_price": prepared.f_tp,
        "session": prepared.signal.session,
        "tick_size": prepared.tick_size,
        "signal_mode": "msi_orderblock",
        "order_kind": "msi_cooldown",
        "chain_id": prepared.chain_id,
        "risk_ob": prepared.risk_ob,
        "alert_payload": payload,
        "market_features": mf,
    }

    await asyncio.to_thread(state_manager.save_active_order_transactional, event_id, state_payload)
    log_struct(
        "info",
        "msi_cooldown",
        f"Karencja {MSI_COOLDOWN_MINUTES} min — Limit odłożony",
        symbol=symbol,
        event_id=event_id,
        chain_id=prepared.chain_id,
        entry=prepared.f_entry,
        sl=prepared.f_sl,
        tp=prepared.f_tp,
        timestamp_signal=prepared.timestamp_signal,
        cooldown_until=cooldown_until.isoformat(),
    )

    elapsed_ms = (perf_counter() - start_total) * 1000.0
    log_struct(
        "info",
        "msi_cooldown",
        f"handle_msi_ob_limit_signal zakończone w {elapsed_ms:.1f} ms (cooldown)",
        symbol=symbol,
        event_id=event_id,
    )


async def _evaluate_cooldown_doc(
    executor: BybitExecutor,
    doc_id: str,
    data: Dict[str, Any],
) -> None:
    """Po upływie karencji: świece 1M → reject lub place."""
    if data.get("status") != "COOLDOWN_PENDING":
        return

    cooldown_until_raw = data.get("cooldown_until_at")
    if not cooldown_until_raw:
        log_struct("error", "msi_cooldown", "Brak cooldown_until_at", event_id=doc_id)
        return

    cooldown_until = parse_signal_ts(cooldown_until_raw)
    now = datetime.now(timezone.utc)
    if now < cooldown_until:
        return

    symbol = _normalize_symbol(str(data.get("symbol", "")))
    direction = str(data.get("direction", "LONG"))
    entry_limit = float(data.get("planned_entry_price", 0))
    timestamp_signal = str(data.get("timestamp_signal", ""))
    chain_id = str(data.get("chain_id", doc_id))

    if entry_limit <= 0 or not timestamp_signal:
        log_struct(
            "error",
            "msi_cooldown",
            "Niekompletne dane COOLDOWN_PENDING",
            event_id=doc_id,
            symbol=symbol,
        )
        state_manager.update_active_order(doc_id, {"status": "ERROR_DATA_MISSING"})
        return

    signal_ts = parse_signal_ts(timestamp_signal)
    start_ms = int(signal_ts.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    try:
        klines = await asyncio.to_thread(
            call_with_retry,
            executor.get_klines_1m,
            symbol,
            start_ms,
            end_ms,
        )
    except Exception as e:
        log_struct(
            "warning",
            "msi_cooldown",
            f"Nie udało się pobrać klines 1M — retry w kolejnym cyklu: {e}",
            event_id=doc_id,
            symbol=symbol,
        )
        return

    failed, min_low, max_high = cooldown_failed_break(direction, entry_limit, klines)

    if failed:
        state_manager.update_active_order(doc_id, {
            "status": "REJECTED_FAILED_BREAK",
            "rejected_at": now.isoformat(),
            "reject_reason": "failed_break_entry_touched",
            "cooldown_min_low": min_low,
            "cooldown_max_high": max_high,
        })
        log_struct(
            "info",
            "msi_cooldown",
            "REJECTED_FAILED_BREAK — entry dotknięty w oknie karencji",
            symbol=symbol,
            event_id=doc_id,
            chain_id=chain_id,
            direction=direction,
            entry=entry_limit,
            min_low=min_low,
            max_high=max_high,
            timestamp_signal=timestamp_signal,
        )
        mf = data.get("market_features") if isinstance(data.get("market_features"), dict) else {}
        log_rejected_failed_break(
            chain_id=chain_id,
            symbol=symbol,
            direction=direction,
            entry_limit=entry_limit,
            sl=float(data.get("planned_sl_price", 0)),
            risk_ob=float(data.get("risk_ob", 0)),
            event_id=doc_id,
            timestamp_signal=timestamp_signal,
            session=data.get("session"),
            cooldown_minutes=int(data.get("cooldown_minutes", MSI_COOLDOWN_MINUTES)),
            min_low=min_low,
            max_high=max_high,
            market_features=mf,
        )
        return

    alert_payload = data.get("alert_payload")
    if not isinstance(alert_payload, dict):
        log_struct(
            "error",
            "msi_cooldown",
            "Brak alert_payload — nie można złożyć limitu",
            event_id=doc_id,
            symbol=symbol,
        )
        state_manager.update_active_order(doc_id, {"status": "ERROR_DATA_MISSING"})
        return

    prepared = await _prepare_msi_order(alert_payload)
    if prepared is None:
        state_manager.update_active_order(doc_id, {"status": "ERROR", "error": "prepare_failed_after_cooldown"})
        return

    log_struct(
        "info",
        "msi_cooldown",
        "Karencja zakończona — składam Limit GTC",
        symbol=symbol,
        event_id=doc_id,
        chain_id=chain_id,
        entry=prepared.f_entry,
        timestamp_signal=prepared.timestamp_signal,
        klines_count=len(klines),
        min_low=min_low,
        max_high=max_high,
    )
    mf = data.get("market_features") if isinstance(data.get("market_features"), dict) else {}
    await _place_msi_limit_gtc(executor, prepared, market_features=mf)


async def _process_msi_cooldown_queue_async(executor: BybitExecutor) -> None:
    docs = list(state_manager.get_orders_by_status("COOLDOWN_PENDING"))
    if not docs:
        return
    log_struct("debug", "msi_cooldown", "Cooldown queue", count=len(docs))
    for doc in docs:
        data = doc.to_dict() or {}
        try:
            await _evaluate_cooldown_doc(executor, doc.id, data)
        except Exception as e:
            log_struct(
                "error",
                "msi_cooldown",
                f"Błąd ewaluacji karencji: {e}",
                event_id=doc.id,
                symbol=data.get("symbol"),
            )


def process_msi_cooldown_queue(executor: BybitExecutor) -> None:
    """Wołane z update_filled_orders — sprawdza czy karencja minęła, bez blokowania alertów."""
    try:
        asyncio.run(_process_msi_cooldown_queue_async(executor))
    except Exception as e:
        log_struct("error", "msi_cooldown", f"process_msi_cooldown_queue failed: {e}")
