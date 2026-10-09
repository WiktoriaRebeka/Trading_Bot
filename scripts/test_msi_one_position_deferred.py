#!/usr/bin/env python3
"""
Jedna pozycja na symbol:
(a) OB przy OPEN → DEFERRED, bez place
(b) 3 OB w trakcie → po zamknięciu place tylko dla 3.
(c) cena za SL przy zamknięciu → SKIPPED_SL_ALREADY_HIT
(d) brak pozycji → place jak dziś (immediate)
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MSI_COOLDOWN_ENABLED"] = "false"


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
    timestamp_signal: str = "2026-10-09T12:00:00+00:00"

    def __post_init__(self):
        if self.order_params is None:
            self.order_params = {"price": "100.5"}
        if self.signal is None:
            self.signal = _fake_signal(direction="LONG" if self.is_long else "SHORT")


@dataclass
class _FakeDoc:
    id: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return dict(self.data)


def test_sl_already_hit_helpers():
    assert moh._sl_already_hit(direction="LONG", planned_sl=100.0, mark_price=99.0) is True
    assert moh._sl_already_hit(direction="LONG", planned_sl=100.0, mark_price=100.0) is True
    assert moh._sl_already_hit(direction="LONG", planned_sl=100.0, mark_price=100.1) is False
    assert moh._sl_already_hit(direction="SHORT", planned_sl=100.0, mark_price=100.5) is True
    assert moh._sl_already_hit(direction="SHORT", planned_sl=100.0, mark_price=99.5) is False


def test_a_ob_while_position_open_defers_no_place():
    prepared = _FakePrepared(event_id="evt-new")
    place_calls: List[Any] = []
    saves: List[Dict[str, Any]] = []

    async def _fake_place(*_a, **_k):
        place_calls.append(True)

    async def _run():
        with patch.object(moh, "MSI_COOLDOWN_ENABLED", False), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_symbol_has_open_position", new=AsyncMock(return_value=True)
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
            side_effect=lambda eid, payload: saves.append(dict(payload)),
        ):
            await moh.handle_msi_ob_limit_signal(
                {"event_id": "evt-new", "symbol": "BTCUSDT"},
                executor=MagicMock(),
            )

    asyncio.run(_run())
    assert place_calls == []
    assert len(saves) == 1
    assert saves[0]["status"] == "DEFERRED_POSITION_OPEN"
    assert saves[0]["order_kind"] == "msi_deferred"


def test_d_no_position_places_immediately():
    prepared = _FakePrepared(event_id="evt-flat")
    place_calls: List[Any] = []

    async def _fake_place(executor, prep, *, market_features=None):
        place_calls.append(prep.event_id)

    async def _run():
        with patch.object(moh, "MSI_COOLDOWN_ENABLED", False), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_symbol_has_open_position", new=AsyncMock(return_value=False)
        ), patch.object(
            moh, "_cancel_superseded_msi_limits", new=AsyncMock(return_value=0)
        ), patch.object(
            moh, "_place_msi_limit_gtc", new=_fake_place
        ), patch.object(
            moh.state_manager, "is_signal_logged", return_value=False
        ), patch.object(
            moh.state_manager, "get_active_order_by_id", return_value=None
        ):
            await moh.handle_msi_ob_limit_signal(
                {"event_id": "evt-flat", "symbol": "BTCUSDT"},
                executor=MagicMock(),
            )

    asyncio.run(_run())
    assert place_calls == ["evt-flat"]


def test_b_three_deferred_only_latest_places_after_close():
    """3 DEFERRED docs → queue cleanup supersedes 1+2, places only 3."""
    docs = [
        _FakeDoc(
            "evt-1",
            {
                "symbol": "BTCUSDT",
                "status": "DEFERRED_POSITION_OPEN",
                "signal_mode": "msi_orderblock",
                "deferred_at": "2026-10-09T10:00:00+00:00",
                "direction": "LONG",
                "planned_sl_price": 100.0,
                "planned_entry_price": 100.5,
                "alert_payload": {"event_id": "evt-1"},
                "market_features": {},
                "chain_id": "c1",
            },
        ),
        _FakeDoc(
            "evt-2",
            {
                "symbol": "BTCUSDT",
                "status": "DEFERRED_POSITION_OPEN",
                "signal_mode": "msi_orderblock",
                "deferred_at": "2026-10-09T11:00:00+00:00",
                "direction": "LONG",
                "planned_sl_price": 100.0,
                "planned_entry_price": 100.5,
                "alert_payload": {"event_id": "evt-2"},
                "market_features": {},
                "chain_id": "c2",
            },
        ),
        _FakeDoc(
            "evt-3",
            {
                "symbol": "BTCUSDT",
                "status": "DEFERRED_POSITION_OPEN",
                "signal_mode": "msi_orderblock",
                "deferred_at": "2026-10-09T12:00:00+00:00",
                "direction": "LONG",
                "planned_sl_price": 100.0,
                "planned_entry_price": 100.5,
                "alert_payload": {"event_id": "evt-3"},
                "market_features": {},
                "chain_id": "c3",
            },
        ),
    ]
    updates: List[tuple] = []
    place_ids: List[str] = []
    prepared = _FakePrepared(event_id="evt-3")

    async def _fake_place(executor, prep, *, market_features=None):
        place_ids.append(prep.event_id)

    async def _run():
        with patch.object(
            moh.state_manager, "get_deferred_msi_orders", return_value=docs
        ), patch.object(
            moh.state_manager,
            "update_active_order",
            side_effect=lambda eid, payload: updates.append((eid, dict(payload))),
        ), patch.object(
            moh, "_symbol_has_open_position", new=AsyncMock(return_value=False)
        ), patch.object(
            moh, "_mark_price_for_symbol", return_value=101.0  # powyżej SL
        ), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_place_msi_limit_gtc", new=_fake_place
        ):
            await moh._process_msi_deferred_queue_async(MagicMock())

    asyncio.run(_run())
    superseded = [u for u in updates if u[1].get("status") == "CANCELLED_SUPERSEDED"]
    assert {u[0] for u in superseded} == {"evt-1", "evt-2"}
    assert place_ids == ["evt-3"]


def test_c_sl_already_hit_skips_place():
    doc = _FakeDoc(
        "evt-sl",
        {
            "symbol": "BTCUSDT",
            "status": "DEFERRED_POSITION_OPEN",
            "signal_mode": "msi_orderblock",
            "direction": "LONG",
            "planned_sl_price": 100.0,
            "planned_entry_price": 100.5,
            "alert_payload": {"event_id": "evt-sl"},
            "market_features": {},
            "chain_id": "c-sl",
        },
    )
    updates: List[Dict[str, Any]] = []
    place_calls: List[Any] = []

    async def _fake_place(*_a, **_k):
        place_calls.append(True)

    async def _run():
        with patch.object(
            moh, "_symbol_has_open_position", new=AsyncMock(return_value=False)
        ), patch.object(
            moh, "_mark_price_for_symbol", return_value=99.5  # za SL
        ), patch.object(
            moh.state_manager,
            "update_active_order",
            side_effect=lambda eid, payload: updates.append(dict(payload)),
        ), patch.object(
            moh, "_place_msi_limit_gtc", new=_fake_place
        ):
            await moh._place_deferred_doc(MagicMock(), "evt-sl", doc.to_dict())

    asyncio.run(_run())
    assert place_calls == []
    assert updates[0]["status"] == "SKIPPED_SL_ALREADY_HIT"


def test_only_deferred_supersede_when_position_open():
    """Przy OPEN: tylko_deferred=True — nie kasujemy PLACED przez Bybit w tym teście."""
    prepared = _FakePrepared(event_id="evt-d2")
    cancel_kwargs: List[Dict[str, Any]] = []

    async def _fake_cancel(*_a, **kwargs):
        cancel_kwargs.append(kwargs)
        return 1

    async def _run():
        with patch.object(moh, "MSI_COOLDOWN_ENABLED", False), patch.object(
            moh, "_prepare_msi_order", new=AsyncMock(return_value=prepared)
        ), patch.object(
            moh, "_symbol_has_open_position", new=AsyncMock(return_value=True)
        ), patch.object(
            moh, "_cancel_superseded_msi_limits", new=_fake_cancel
        ), patch.object(
            moh, "_save_deferred_ob", new=AsyncMock()
        ), patch.object(
            moh.state_manager, "is_signal_logged", return_value=False
        ), patch.object(
            moh.state_manager, "get_active_order_by_id", return_value=None
        ):
            await moh.handle_msi_ob_limit_signal(
                {"event_id": "evt-d2", "symbol": "BTCUSDT"},
                executor=MagicMock(),
            )

    asyncio.run(_run())
    assert cancel_kwargs and cancel_kwargs[0].get("only_deferred") is True


if __name__ == "__main__":
    test_sl_already_hit_helpers()
    test_a_ob_while_position_open_defers_no_place()
    test_d_no_position_places_immediately()
    test_b_three_deferred_only_latest_places_after_close()
    test_c_sl_already_hit_skips_place()
    test_only_deferred_supersede_when_position_open()
    print("OK — test_msi_one_position_deferred")
