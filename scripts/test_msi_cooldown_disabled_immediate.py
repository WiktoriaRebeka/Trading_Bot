#!/usr/bin/env python3
"""
OB_NEW → Limit GTC w tym samym cyklu gdy MSI_COOLDOWN_ENABLED=false.
Bez COOLDOWN_PENDING i bez failed-break.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Flaga OFF zanim zaimportujemy handler (czyta env przy imporcie cooldown_logic).
os.environ["MSI_COOLDOWN_ENABLED"] = "false"


def _ensure_mod(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if isinstance(mod, types.ModuleType) and not isinstance(mod, MagicMock):
        return mod
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _stub_heavy_deps() -> None:
    """Lokalnie (Py3.14): stubuj google/flask/requests zanim handler ściągnie Cloud Run deps."""
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

from bot_service import msi_cooldown_logic as cd
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
    symbol: str = "BTCUSDT"
    chain_id: str = "BTCUSDT-1"
    signal: Any = None
    tick_size: str = "0.1"
    qty_step: str = "0.001"
    is_long: bool = True
    f_entry: float = 100.5
    f_sl: float = 100.0
    f_tp: float = 101.5
    risk_ob: float = 0.5
    calculated_qty: float = 0.01
    sl_distance: float = 0.5
    order_params: Optional[Dict[str, Any]] = None
    timestamp_signal: str = "2026-10-08T12:00:00+00:00"

    def __post_init__(self):
        if self.order_params is None:
            self.order_params = {"price": "100.5"}
        if self.signal is None:
            self.signal = _fake_signal(direction="LONG" if self.is_long else "SHORT")


def test_flag_default_off():
    assert cd.MSI_COOLDOWN_ENABLED is False


def test_ob_new_places_limit_immediately_no_cooldown_pending():
    prepared = _FakePrepared()
    place_calls: List[Any] = []
    save_calls: List[Dict[str, Any]] = []

    async def _fake_place(executor, prep, *, market_features=None):
        place_calls.append((prep.event_id, prep.f_entry, market_features))

    async def _run():
        with patch.object(moh, "MSI_COOLDOWN_ENABLED", False), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_cancel_superseded_msi_limits", new=AsyncMock(return_value=0)
        ), patch.object(
            moh, "_place_msi_limit_gtc", new=_fake_place
        ), patch.object(
            moh.state_manager, "is_signal_logged", return_value=False
        ), patch.object(
            moh.state_manager, "get_active_order_by_id", return_value=None
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: save_calls.append(dict(payload)),
        ):
            await moh.handle_msi_ob_limit_signal(
                {"event_id": "evt-1", "symbol": "BTCUSDT"},
                executor=MagicMock(),
            )

    asyncio.run(_run())

    assert len(place_calls) == 1, place_calls
    assert place_calls[0][0] == "evt-1"
    assert place_calls[0][1] == 100.5
    assert not any(s.get("status") == "COOLDOWN_PENDING" for s in save_calls)


def test_cooldown_enabled_still_writes_pending():
    prepared = _FakePrepared()
    place_calls: List[Any] = []
    save_calls: List[Dict[str, Any]] = []

    async def _fake_place(*_a, **_k):
        place_calls.append(True)

    async def _run():
        with patch.object(moh, "MSI_COOLDOWN_ENABLED", True), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_cancel_superseded_msi_limits", new=AsyncMock(return_value=0)
        ), patch.object(
            moh, "_place_msi_limit_gtc", new=_fake_place
        ), patch.object(
            moh.state_manager, "is_signal_logged", return_value=False
        ), patch.object(
            moh.state_manager, "get_active_order_by_id", return_value=None
        ), patch.object(
            moh.state_manager,
            "save_active_order_transactional",
            side_effect=lambda eid, payload: save_calls.append(dict(payload)),
        ):
            await moh.handle_msi_ob_limit_signal(
                {"event_id": "evt-1", "symbol": "BTCUSDT"},
                executor=MagicMock(),
            )

    asyncio.run(_run())

    assert place_calls == []
    assert len(save_calls) == 1
    assert save_calls[0]["status"] == "COOLDOWN_PENDING"
    assert save_calls[0]["order_kind"] == "msi_cooldown"


def test_marketable_log_long_when_mark_at_or_below_entry():
    """Log INFO bez blokady — wywołanie _place nadal idzie dalej."""
    prepared = _FakePrepared(is_long=True, f_entry=100.5)
    logs: List[str] = []

    def _capture(level, stage, msg, **fields):
        logs.append(msg)

    async def _run():
        with patch.object(moh, "log_struct", side_effect=_capture), patch.object(
            moh, "_mark_price_for_symbol", return_value=100.0
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
            new=AsyncMock(return_value={"orderId": "oid-1"}),
        ), patch.object(
            moh, "log_order_placed"
        ), patch.object(
            moh.state_manager, "update_active_order"
        ):
            await moh._place_msi_limit_gtc(MagicMock(), prepared, market_features={})

    asyncio.run(_run())
    assert "[MSI] limit marketable at placement" in logs


if __name__ == "__main__":
    test_flag_default_off()
    test_ob_new_places_limit_immediately_no_cooldown_pending()
    test_cooldown_enabled_still_writes_pending()
    test_marketable_log_long_when_mark_at_or_below_entry()
    print("OK — test_msi_cooldown_disabled_immediate")
