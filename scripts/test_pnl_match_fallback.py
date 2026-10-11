#!/usr/bin/env python3
"""
Testy fallbacku closed-PnL → active_orders (_find_matching_order krok 3):
(a) jeden OPEN → match
(b) OPEN + CANCELLED_SUPERSEDED → wybiera OPEN
(c) brak OPEN → None + WARNING ambiguous
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

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


@dataclass
class _FakeDoc:
    id: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.data)


def _pnl(*, symbol: str = "BTCUSDT", side: str = "Sell", order_id: str = "oid-close") -> Dict[str, Any]:
    """closed-PnL: side Sell zamyka LONG."""
    return {"symbol": symbol, "side": side, "orderId": order_id, "orderLinkId": None}


def test_a_single_open_matches():
    open_doc = _FakeDoc(
        "evt-open",
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "event_id": "evt-open",
            "planned_entry_price": 100.0,
        },
    )
    logs: List[tuple] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg, fields))

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_open_orders_for_symbol_side", return_value=[open_doc]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl())

    assert match is not None
    assert match["id"] == "evt-open"
    assert match["planned_entry_price"] == 100.0
    assert any(m[1] == "[PNL] fallback match used" and m[0] == "info" for m in logs)
    assert not any(m[1] == "[PNL] fallback match ambiguous" for m in logs)


def test_b_open_plus_cancelled_picks_open():
    """get_open_orders_for_symbol_side zwraca tylko OPEN — CANCELLED nie wchodzi."""
    open_doc = _FakeDoc(
        "evt-open",
        {"symbol": "COMPUSDT", "direction": "SHORT", "status": "OPEN", "event_id": "evt-open"},
    )
    # Symulacja: state_manager filtruje — zwraca tylko OPEN (nie CANCELLED_SUPERSEDED).
    logs: List[tuple] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg, fields))

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_open_orders_for_symbol_side", return_value=[open_doc]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        # closed SHORT → side Buy w PnL Bybit
        match = bl._find_matching_order(
            {"symbol": "COMPUSDT", "side": "Buy", "orderId": "oid-2", "orderLinkId": None}
        )

    assert match is not None
    assert match["id"] == "evt-open"
    used = [m for m in logs if m[1] == "[PNL] fallback match used"]
    assert used and used[0][2].get("candidates") == 1


def test_c_no_open_returns_none_with_warning():
    logs: List[tuple] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg, fields))

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_open_orders_for_symbol_side", return_value=[]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(order_id="oid-none"))

    assert match is None
    assert any(m[1] == "[PNL] fallback match used" for m in logs)
    amb = [m for m in logs if m[1] == "[PNL] fallback match ambiguous"]
    assert amb and amb[0][0] == "warning"
    assert amb[0][2].get("candidates") == 0
    assert amb[0][2].get("orderId") == "oid-none"


def test_c2_two_opens_ambiguous():
    docs = [
        _FakeDoc("e1", {"symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN"}),
        _FakeDoc("e2", {"symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN"}),
    ]
    logs: List[tuple] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg, fields))

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_open_orders_for_symbol_side", return_value=docs
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl())

    assert match is None
    amb = [m for m in logs if m[1] == "[PNL] fallback match ambiguous"]
    assert amb and amb[0][2].get("candidates") == 2


def test_get_open_orders_filters_cancelled():
    """Integracja ze state_manager: filtr status==OPEN na streamie."""
    from bot_service import state_manager as sm

    open_d = _FakeDoc(
        "open1",
        {"symbol": "IMXUSDT", "direction": "LONG", "status": "OPEN"},
    )
    cancelled = _FakeDoc(
        "canc1",
        {"symbol": "IMXUSDT", "direction": "LONG", "status": "CANCELLED_SUPERSEDED"},
    )

    class _Coll:
        def stream(self):
            return [open_d, cancelled]

    with patch.object(sm, "_collection_active_orders", return_value=_Coll()):
        docs = sm.get_open_orders_for_symbol_side("IMXUSDT", "LONG")

    assert len(docs) == 1
    assert docs[0].id == "open1"


if __name__ == "__main__":
    test_a_single_open_matches()
    test_b_open_plus_cancelled_picks_open()
    test_c_no_open_returns_none_with_warning()
    test_c2_two_opens_ambiguous()
    test_get_open_orders_filters_cancelled()
    print("OK — test_pnl_match_fallback")
