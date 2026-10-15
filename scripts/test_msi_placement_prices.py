#!/usr/bin/env python3
"""
Testy cen przy MSI place:
- lookup tickera z/bez .P
- guard SL na lastPrice → SKIPPED
- marketable LONG/SHORT (ask/bid) → log + flaga
- brak ceny → WARNING
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ensure_mod(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if isinstance(mod, types.ModuleType) and not isinstance(mod, MagicMock):
        return mod
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _stub_heavy_deps() -> None:
    google = _ensure_mod("google")
    cloud = _ensure_mod("google.cloud")
    google.cloud = cloud  # type: ignore[attr-defined]
    firestore = _ensure_mod("google.cloud.firestore")
    cloud.firestore = firestore  # type: ignore[attr-defined]
    firestore.Client = MagicMock  # type: ignore[attr-defined]
    firestore.SERVER_TIMESTAMP = object()  # type: ignore[attr-defined]
    firestore.Query = MagicMock  # type: ignore[attr-defined]
    _ensure_mod("google.cloud.bigquery")
    api_core = _ensure_mod("google.api_core")
    google.api_core = api_core  # type: ignore[attr-defined]
    gexc = _ensure_mod("google.api_core.exceptions")
    api_core.exceptions = gexc  # type: ignore[attr-defined]
    gexc.GoogleAPICallError = type("GoogleAPICallError", (Exception,), {})  # type: ignore[attr-defined]
    gexc.Aborted = type("Aborted", (Exception,), {})  # type: ignore[attr-defined]
    _ensure_mod("google.protobuf")
    flask = _ensure_mod("flask")
    flask.current_app = MagicMock()  # type: ignore[attr-defined]
    requests = _ensure_mod("requests")
    rexc = _ensure_mod("requests.exceptions")
    requests.exceptions = rexc  # type: ignore[attr-defined]
    rexc.RequestException = type("RequestException", (Exception,), {})  # type: ignore[attr-defined]
    rexc.JSONDecodeError = type("JSONDecodeError", (Exception,), {})  # type: ignore[attr-defined]
    requests.Session = MagicMock  # type: ignore[attr-defined]


_stub_heavy_deps()

from bot_service import bot_logic as bl
from bot_service import msi_order_handler as moh


def _fake_signal(*, direction: str = "LONG") -> MagicMock:
    sig = MagicMock()
    sig.direction = direction
    sig.signal_id = "sig-1"
    sig.session = "ASIA"
    sig.market_features = {}
    return sig


@dataclass
class _FakePrepared:
    event_id: str = "evt-1"
    symbol: str = "KASUSDT"
    chain_id: str = "KASUSDT-1"
    signal: Any = None
    tick_size: str = "0.00001"
    qty_step: str = "1"
    is_long: bool = True
    f_entry: float = 0.04240
    f_sl: float = 0.04230
    f_tp: float = 0.04260
    risk_ob: float = 0.00010
    calculated_qty: float = 10.0
    sl_distance: float = 0.00010
    order_params: Optional[Dict[str, Any]] = None
    timestamp_signal: str = "2026-10-12T05:56:00+00:00"

    def __post_init__(self):
        if self.order_params is None:
            self.order_params = {"price": str(self.f_entry)}
        if self.signal is None:
            self.signal = _fake_signal(direction="LONG" if self.is_long else "SHORT")


def test_ticker_lookup_keys_both_forms():
    assert "KASUSDT" in bl._ticker_lookup_keys("KASUSDT")
    assert "KASUSDT.P" in bl._ticker_lookup_keys("KASUSDT")
    assert "KASUSDT" in bl._ticker_lookup_keys("KASUSDT.P")
    assert "KASUSDT.P" in bl._ticker_lookup_keys("KASUSDT.P")


def test_get_ticker_finds_dot_p_key_when_caller_uses_bare_symbol():
    executor = MagicMock()
    executor.get_latest_prices.return_value = {
        "KASUSDT.P": {
            "markPrice": "0.0425",
            "lastPrice": "0.0424",
            "ask1Price": "0.04241",
            "bid1Price": "0.04239",
        }
    }
    ticker = bl._get_ticker_for_symbol(executor, "KASUSDT")
    assert ticker is not None
    assert ticker["lastPrice"] == "0.0424"
    prices = bl._placement_prices_for_symbol(executor, "KASUSDT")
    assert prices["last"] == 0.0424
    assert prices["ask"] == 0.04241
    assert prices["bid"] == 0.04239


def test_get_ticker_finds_bare_key_when_caller_uses_dot_p():
    executor = MagicMock()
    executor.get_latest_prices.return_value = {
        "KASUSDT": {"lastPrice": "1.5", "ask1Price": "1.51", "bid1Price": "1.49"}
    }
    assert bl._get_ticker_for_symbol(executor, "KASUSDT.P") is not None
    assert bl._mark_price_for_symbol(executor, "KASUSDT.P") == 1.5


def test_limit_is_marketable_long_short():
    assert moh._limit_is_marketable(is_long=True, entry=100.0, ask=99.9, bid=99.8) is True
    assert moh._limit_is_marketable(is_long=True, entry=100.0, ask=100.1, bid=100.0) is False
    assert moh._limit_is_marketable(is_long=False, entry=100.0, ask=100.1, bid=100.0) is True
    assert moh._limit_is_marketable(is_long=False, entry=100.0, ask=100.1, bid=99.9) is False
    assert moh._limit_is_marketable(is_long=True, entry=100.0, ask=None, bid=99.0) is False


def test_skip_long_when_last_at_or_beyond_sl():
    prepared = _FakePrepared(is_long=True, f_entry=100.5, f_sl=100.0)
    prepared.f_entry = 100.5
    prepared.f_sl = 100.0
    saves: List[Dict[str, Any]] = []
    place_api: List[Any] = []

    async def _run():
        with patch.object(
            moh,
            "_placement_prices_for_symbol",
            return_value={"last": 99.9, "ask": 100.0, "bid": 99.8, "mark": 99.9},
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: saves.append(dict(payload)),
        ), patch.object(
            moh, "async_call_with_retry",
            new=AsyncMock(side_effect=lambda *a, **k: place_api.append(True)),
        ):
            await moh._place_msi_limit_gtc(MagicMock(), prepared, market_features={})

    asyncio.run(_run())
    assert place_api == []
    assert saves and saves[0]["status"] == "SKIPPED_SL_ALREADY_HIT"
    assert saves[0]["last_price_at_skip"] == 99.9


def test_skip_short_when_last_at_or_beyond_sl():
    prepared = _FakePrepared(
        is_long=False, f_entry=100.0, f_sl=100.5, signal=_fake_signal(direction="SHORT")
    )
    prepared.signal.direction = "SHORT"
    saves: List[Dict[str, Any]] = []
    place_api: List[Any] = []

    async def _run():
        with patch.object(
            moh,
            "_placement_prices_for_symbol",
            return_value={"last": 100.6, "ask": 100.7, "bid": 100.5, "mark": 100.6},
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: saves.append(dict(payload)),
        ), patch.object(
            moh, "async_call_with_retry",
            new=AsyncMock(side_effect=lambda *a, **k: place_api.append(True)),
        ):
            await moh._place_msi_limit_gtc(MagicMock(), prepared, market_features={})

    asyncio.run(_run())
    assert place_api == []
    assert saves and saves[0]["status"] == "SKIPPED_SL_ALREADY_HIT"


def test_marketable_long_logs_and_sets_flag():
    prepared = _FakePrepared(is_long=True, f_entry=100.5, f_sl=100.0)
    prepared.f_entry = 100.5
    prepared.f_sl = 100.0
    logs: List[str] = []
    saves: List[Dict[str, Any]] = []
    placed_bq: List[Dict[str, Any]] = []
    updates: List[Dict[str, Any]] = []

    def _capture(level, stage, msg, **fields):
        logs.append(msg)

    async def _run():
        with patch.object(moh, "log_struct", side_effect=_capture), patch.object(
            moh,
            "_placement_prices_for_symbol",
            # last powyżej SL; ask <= entry → marketable LONG
            return_value={"last": 100.2, "ask": 100.4, "bid": 100.1, "mark": 100.2},
        ), patch.object(
            moh, "_ensure_sl_valid_for_bybit", return_value=prepared.f_sl
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: saves.append(dict(payload)),
        ), patch.object(
            moh, "_build_signal_analysis_data", return_value={}
        ), patch.object(
            moh, "_submit_signal_analytics"
        ), patch.object(
            moh, "async_call_with_retry",
            new=AsyncMock(return_value={"orderId": "oid-1"}),
        ), patch.object(
            moh,
            "log_order_placed",
            side_effect=lambda **kw: placed_bq.append(kw),
        ), patch.object(
            moh.state_manager,
            "update_active_order",
            side_effect=lambda eid, payload: updates.append(dict(payload)),
        ), patch.object(moh.asyncio, "sleep", new=AsyncMock()):
            ex = MagicMock()
            ex.find_sl_tp_order_ids.return_value = ("sl-oid", "tp-oid")
            await moh._place_msi_limit_gtc(ex, prepared, market_features={})

    asyncio.run(_run())
    assert "[MSI] limit marketable at placement" in logs
    assert saves and saves[0].get("placed_marketable") is True
    assert saves[0].get("ask_at_placement") == 100.4
    assert saves[0].get("bid_at_placement") == 100.1
    assert saves[0].get("price_at_placement") == 100.2
    placed_u = [u for u in updates if u.get("status") == "PLACED"]
    assert placed_u and placed_u[0].get("placed_marketable") is True
    assert placed_u[0].get("slOrderId") == "sl-oid"
    assert placed_u[0].get("tpOrderId") == "tp-oid"
    assert placed_u[0].get("entryOrderId") == "oid-1"
    assert placed_bq and placed_bq[0].get("placed_marketable") is True
    assert placed_bq[0].get("ask_at_placement") == 100.4


def test_marketable_short_logs_and_sets_flag():
    prepared = _FakePrepared(
        is_long=False, f_entry=100.0, f_sl=100.5, signal=_fake_signal(direction="SHORT")
    )
    prepared.signal.direction = "SHORT"
    prepared.f_entry = 100.0
    prepared.f_sl = 100.5
    logs: List[str] = []
    saves: List[Dict[str, Any]] = []

    def _capture(level, stage, msg, **fields):
        logs.append(msg)

    async def _run():
        with patch.object(moh, "log_struct", side_effect=_capture), patch.object(
            moh,
            "_placement_prices_for_symbol",
            # last poniżej SL; bid >= entry → marketable SHORT
            return_value={"last": 100.2, "ask": 100.3, "bid": 100.1, "mark": 100.2},
        ), patch.object(
            moh, "_ensure_sl_valid_for_bybit", return_value=prepared.f_sl
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: saves.append(dict(payload)),
        ), patch.object(
            moh, "_build_signal_analysis_data", return_value={}
        ), patch.object(
            moh, "_submit_signal_analytics"
        ), patch.object(
            moh, "async_call_with_retry",
            new=AsyncMock(return_value={"orderId": "oid-s"}),
        ), patch.object(
            moh, "log_order_placed"
        ), patch.object(
            moh.state_manager, "update_active_order"
        ), patch.object(moh.asyncio, "sleep", new=AsyncMock()):
            ex = MagicMock()
            ex.find_sl_tp_order_ids.return_value = (None, None)
            await moh._place_msi_limit_gtc(ex, prepared, market_features={})

    asyncio.run(_run())
    assert "[MSI] limit marketable at placement" in logs
    assert saves and saves[0].get("placed_marketable") is True


def test_price_unavailable_warning_still_places():
    prepared = _FakePrepared(is_long=True, f_entry=100.5, f_sl=100.0)
    prepared.f_entry = 100.5
    prepared.f_sl = 100.0
    logs: List[tuple] = []
    place_api: List[Any] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg))

    async def _run():
        with patch.object(moh, "log_struct", side_effect=_capture), patch.object(
            moh,
            "_placement_prices_for_symbol",
            return_value={"last": None, "ask": None, "bid": None, "mark": None},
        ), patch.object(
            moh, "_ensure_sl_valid_for_bybit", return_value=prepared.f_sl
        ), patch.object(
            moh.state_manager, "save_active_order_transactional"
        ), patch.object(
            moh, "_build_signal_analysis_data", return_value={}
        ), patch.object(
            moh, "_submit_signal_analytics"
        ), patch.object(
            moh, "async_call_with_retry",
            new=AsyncMock(return_value={"orderId": "oid-2"}),
        ), patch.object(
            moh, "log_order_placed"
        ), patch.object(
            moh.state_manager, "update_active_order"
        ), patch.object(moh.asyncio, "sleep", new=AsyncMock()):
            ex = MagicMock()
            ex.find_sl_tp_order_ids.return_value = (None, None)
            await moh._place_msi_limit_gtc(ex, prepared, market_features={})
            place_api.append(True)

    asyncio.run(_run())
    assert any(
        level == "warning" and msg == "[MSI] price unavailable at placement"
        for level, msg in logs
    )
    assert place_api == [True]
    assert not any("limit marketable" in m for _, m in logs)


if __name__ == "__main__":
    test_ticker_lookup_keys_both_forms()
    test_get_ticker_finds_dot_p_key_when_caller_uses_bare_symbol()
    test_get_ticker_finds_bare_key_when_caller_uses_dot_p()
    test_limit_is_marketable_long_short()
    test_skip_long_when_last_at_or_beyond_sl()
    test_skip_short_when_last_at_or_beyond_sl()
    test_marketable_long_logs_and_sets_flag()
    test_marketable_short_logs_and_sets_flag()
    test_price_unavailable_warning_still_places()
    print("OK — test_msi_placement_prices")
