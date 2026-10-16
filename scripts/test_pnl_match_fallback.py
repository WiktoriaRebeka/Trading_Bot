#!/usr/bin/env python3
"""
Testy dopasowania closed-PnL → active_orders (_find_matching_order):
(a) fallback: jeden OPEN → match via fallback
(b) OPEN + CANCELLED → wybiera OPEN
(c) brak OPEN / wiele OPEN → ambiguous
(d) match via sl / tp
(e) match via bybit_bridge
(f) match via link
(j) CLOSED_UNVERIFIED + brak sl/tp → match via entry_price (AAVE)
(k) entry_price: >1 kandydat → najnowszy przed wejściem
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


def _pnl(
    *,
    symbol: str = "BTCUSDT",
    side: str = "Sell",
    order_id: str = "oid-close",
    link: Any = None,
    updated_time: Any = "1700000100000",
) -> Dict[str, Any]:
    """closed-PnL: side Sell zamyka LONG."""
    return {
        "symbol": symbol,
        "side": side,
        "orderId": order_id,
        "orderLinkId": link,
        "updatedTime": updated_time,
    }


def _capture_logs():
    logs: List[tuple] = []

    def _capture(level, stage, msg, **fields):
        logs.append((level, msg, fields))

    return logs, _capture


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
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[open_doc]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(), executor=None)

    assert match is not None
    assert match["id"] == "evt-open"
    assert match["planned_entry_price"] == 100.0
    assert any(m[1] == "[PNL] match via fallback" and m[0] == "info" for m in logs)
    assert not any(m[1] == "[PNL] fallback match ambiguous" for m in logs)


def test_b_open_plus_cancelled_picks_open():
    open_doc = _FakeDoc(
        "evt-open",
        {"symbol": "COMPUSDT", "direction": "SHORT", "status": "OPEN", "event_id": "evt-open"},
    )
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[open_doc]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            {"symbol": "COMPUSDT", "side": "Buy", "orderId": "oid-2", "orderLinkId": None},
            executor=None,
        )

    assert match is not None
    assert match["id"] == "evt-open"
    used = [m for m in logs if m[1] == "[PNL] match via fallback"]
    assert used and used[0][2].get("candidates") == 1


def test_c_no_open_returns_none_with_warning():
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(order_id="oid-none"), executor=None)

    assert match is None
    amb = [m for m in logs if m[1] == "[PNL] fallback match ambiguous"]
    assert amb and amb[0][0] == "warning"
    assert amb[0][2].get("candidates") == 0
    assert amb[0][2].get("orderId") == "oid-none"


def test_c2_two_opens_ambiguous():
    docs = [
        _FakeDoc("e1", {"symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN"}),
        _FakeDoc("e2", {"symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN"}),
    ]
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=docs
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(), executor=None)

    assert match is None
    amb = [m for m in logs if m[1] == "[PNL] fallback match ambiguous"]
    assert amb and amb[0][2].get("candidates") == 2


def test_d_match_via_sl():
    logs, _capture = _capture_logs()
    sl_match = {"id": "evt-sl", "slOrderId": "oid-sl", "symbol": "BTCUSDT"}

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=sl_match
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(order_id="oid-sl"), executor=None)

    assert match is sl_match
    assert any(m[1] == "[PNL] match via sl" for m in logs)


def test_d2_match_via_tp():
    logs, _capture = _capture_logs()
    tp_match = {"id": "evt-tp", "tpOrderId": "oid-tp", "symbol": "ETHUSDT"}

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=tp_match
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            _pnl(symbol="ETHUSDT", order_id="oid-tp"), executor=None
        )

    assert match is tp_match
    assert any(m[1] == "[PNL] match via tp" for m in logs)


def test_e_match_via_bybit_bridge():
    open_a = _FakeDoc("evt-a", {"symbol": "KASUSDT", "direction": "SHORT", "status": "OPEN"})
    open_b = _FakeDoc("evt-b", {"symbol": "KASUSDT", "direction": "SHORT", "status": "OPEN"})
    logs, _capture = _capture_logs()
    executor = MagicMock()

    def _filled(symbol, link_id):
        if link_id == "evt-b":
            return {"orderStatus": "Filled", "orderId": "entry-b", "updatedTime": "1700000000000"}
        return None

    executor.get_filled_entry_order_by_link_id.side_effect = _filled

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[open_a, open_b]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        # closed SHORT → side Buy
        match = bl._find_matching_order(
            {
                "symbol": "KASUSDT",
                "side": "Buy",
                "orderId": "close-x",
                "orderLinkId": None,
                "updatedTime": "1700000100000",
            },
            executor=executor,
        )

    assert match is not None
    assert match["id"] == "evt-b"
    assert any(m[1] == "[PNL] match via bybit_bridge" for m in logs)
    assert not any(m[1] == "[PNL] match via fallback" for m in logs)


def test_f_match_via_link():
    logs, _capture = _capture_logs()
    link_match = {"id": "evt-link", "symbol": "BTCUSDT"}

    with patch.object(
        bl.state_manager, "get_active_order_by_id", return_value=link_match
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(_pnl(link="evt-link"), executor=None)

    assert match is link_match
    assert any(m[1] == "[PNL] match via link" for m in logs)


def test_get_open_orders_filters_cancelled():
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


def test_collect_open_order_ids_sets_entry_sl_tp():
    executor = MagicMock()
    executor.find_sl_tp_order_ids.return_value = ("sl-1", "tp-1")
    data = {"orderId": "entry-1", "planned_sl_price": 1.0, "planned_tp_price": 2.0}

    with patch.object(bl, "call_with_retry", side_effect=lambda fn, *a, **k: fn(*a, **k)):
        out = bl._collect_open_order_ids(executor, "BTCUSDT", data, "evt-1", retries=1)

    assert out == {"entryOrderId": "entry-1", "slOrderId": "sl-1", "tpOrderId": "tp-1"}


def test_g_placed_with_sl_matches_before_open():
    """Pozycja zamknięta zanim updater → OPEN: dokument PLACED + slOrderId → match."""
    logs, _capture = _capture_logs()
    placed = {
        "id": "evt-fast",
        "symbol": "TAOUSDT",
        "direction": "SHORT",
        "status": "PLACED",
        "slOrderId": "oid-sl-fast",
        "tpOrderId": "oid-tp-fast",
        "orderId": "oid-entry",
        "chain_id": "TAOUSDT-1",
        "planned_entry_price": 500.0,
        "planned_sl_price": 505.0,
        "planned_tp_price": 490.0,
        "signal_mode": "msi_orderblock",
    }

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=placed
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            {
                "symbol": "TAOUSDT",
                "side": "Buy",  # zamknięcie SHORT
                "orderId": "oid-sl-fast",
                "orderLinkId": None,
                "updatedTime": "1700000100000",
            },
            executor=None,
        )

    assert match is placed
    assert match["status"] == "PLACED"
    assert any(m[1] == "[PNL] match via sl" for m in logs)


def test_h_bridge_matches_placed_doc():
    """Most Bybit: filled entry na PLACED (nie OPEN) przed czasem zamknięcia."""
    placed = _FakeDoc(
        "evt-placed",
        {
            "symbol": "AAVEUSDT",
            "direction": "SHORT",
            "status": "PLACED",
            "orderId": "entry-1",
        },
    )
    logs, _capture = _capture_logs()
    executor = MagicMock()
    executor.get_filled_entry_order_by_link_id.return_value = {
        "orderStatus": "Filled",
        "orderId": "entry-1",
        "updatedTime": "1700000000000",
    }

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[placed]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            {
                "symbol": "AAVEUSDT",
                "side": "Buy",
                "orderId": "close-sl",
                "orderLinkId": None,
                "updatedTime": "1700000100000",
            },
            executor=executor,
        )

    assert match is not None
    assert match["id"] == "evt-placed"
    assert match["status"] == "PLACED"
    assert any(m[1] == "[PNL] match via bybit_bridge" for m in logs)


def test_i_pnl_candidates_include_placed():
    from bot_service import state_manager as sm

    open_d = _FakeDoc("o1", {"symbol": "DOTUSDT", "direction": "LONG", "status": "OPEN"})
    placed_d = _FakeDoc("p1", {"symbol": "DOTUSDT", "direction": "LONG", "status": "PLACED"})
    cancelled = _FakeDoc(
        "c1", {"symbol": "DOTUSDT", "direction": "LONG", "status": "CANCELLED_SUPERSEDED"}
    )

    class _Coll:
        def stream(self):
            return [open_d, placed_d, cancelled]

    with patch.object(sm, "_collection_active_orders", return_value=_Coll()):
        docs = sm.get_pnl_match_candidate_orders("DOTUSDT", "LONG")
        opens_only = sm.get_open_orders_for_symbol_side("DOTUSDT", "LONG")

    assert {d.id for d in docs} == {"o1", "p1"}
    assert len(opens_only) == 1 and opens_only[0].id == "o1"


def test_j_aave_closed_unverified_match_via_entry_price():
    """
    AAVEUSDT LONG 2026-10-16: CLOSED_UNVERIFIED, brak slOrderId/tpOrderId,
    fallback candidates:0 → match via entry_price (160.83 ± 1 tick).
    """
    event_id = "AAVEUSDT-1792138620000-100"
    unverified = _FakeDoc(
        event_id,
        {
            "symbol": "AAVEUSDT",
            "direction": "LONG",
            "status": "CLOSED_UNVERIFIED",
            "entryOrderId": "4e0723ae-entry",
            "planned_entry_price": 160.83,
            "planned_sl_price": 160.45,
            "tick_size": "0.01",
            "created_at": "2026-10-16T08:39:15+00:00",
        },
    )
    # 2026-10-16 09:37:33 UTC
    entry_ms = 1792143453000
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_pnl_entry_price_match_candidates", return_value=[unverified]
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            {
                "symbol": "AAVEUSDT",
                "side": "Sell",
                "orderId": "c871a810-8622-4c42-9887-cdf47e5c493d",
                "orderLinkId": None,
                "avgEntryPrice": "160.83",
                "avgExitPrice": "160.64",
                "closedPnl": "-1.12",
                "createdTime": str(entry_ms),
                "updatedTime": str(entry_ms),
            },
            executor=None,
        )

    assert match is not None
    assert match["id"] == event_id
    assert match["status"] == "CLOSED_UNVERIFIED"
    assert any(m[1] == "[PNL] match via entry_price" for m in logs)
    assert not any(m[1] == "[PNL] fallback match ambiguous" for m in logs)


def test_k_entry_price_ambiguous_picks_newest_before_entry():
    older = _FakeDoc(
        "AAVE-old",
        {
            "symbol": "AAVEUSDT",
            "direction": "LONG",
            "status": "CANCELLED_SUPERSEDED",
            "planned_entry_price": 160.83,
            "tick_size": "0.01",
            "created_at": "2026-10-16T07:00:00+00:00",
        },
    )
    newer = _FakeDoc(
        "AAVEUSDT-1792138620000-100",
        {
            "symbol": "AAVEUSDT",
            "direction": "LONG",
            "status": "CLOSED_UNVERIFIED",
            "planned_entry_price": 160.83,
            "tick_size": "0.01",
            "created_at": "2026-10-16T08:39:15+00:00",
        },
    )
    entry_ms = 1792143453000
    logs, _capture = _capture_logs()

    with patch.object(bl.state_manager, "get_active_order_by_id", return_value=None), patch.object(
        bl.state_manager, "get_active_order_by_sl_order_id", return_value=None
    ), patch.object(
        bl.state_manager, "get_active_order_by_tp_order_id", return_value=None
    ), patch.object(
        bl.state_manager,
        "get_pnl_entry_price_match_candidates",
        return_value=[older, newer],
    ), patch.object(
        bl.state_manager, "get_pnl_match_candidate_orders", return_value=[]
    ), patch.object(bl, "log_struct", side_effect=_capture):
        match = bl._find_matching_order(
            {
                "symbol": "AAVEUSDT",
                "side": "Sell",
                "orderId": "c871a810-close",
                "orderLinkId": None,
                "avgEntryPrice": "160.83",
                "createdTime": str(entry_ms),
                "updatedTime": str(entry_ms),
            },
            executor=None,
        )

    assert match is not None
    assert match["id"] == "AAVEUSDT-1792138620000-100"
    assert any(
        m[1] == "[PNL] entry_price match ambiguous — picking newest before entry"
        for m in logs
    )
    assert any(m[1] == "[PNL] match via entry_price" for m in logs)


def test_l_entry_price_candidates_include_closed_unverified():
    from bot_service import state_manager as sm

    cu = _FakeDoc(
        "cu1",
        {"symbol": "AAVEUSDT", "direction": "LONG", "status": "CLOSED_UNVERIFIED"},
    )
    placed = _FakeDoc(
        "p1", {"symbol": "AAVEUSDT", "direction": "LONG", "status": "PLACED"}
    )
    stale = _FakeDoc(
        "s1", {"symbol": "AAVEUSDT", "direction": "LONG", "status": "CLOSED_STALE"}
    )

    class _Coll:
        def stream(self):
            return [cu, placed, stale]

    with patch.object(sm, "_collection_active_orders", return_value=_Coll()):
        docs = sm.get_pnl_entry_price_match_candidates("AAVEUSDT", "LONG")
        fallback = sm.get_pnl_match_candidate_orders("AAVEUSDT", "LONG")

    assert {d.id for d in docs} == {"cu1", "p1", "s1"}
    assert {d.id for d in fallback} == {"p1"}


if __name__ == "__main__":
    test_a_single_open_matches()
    test_b_open_plus_cancelled_picks_open()
    test_c_no_open_returns_none_with_warning()
    test_c2_two_opens_ambiguous()
    test_d_match_via_sl()
    test_d2_match_via_tp()
    test_e_match_via_bybit_bridge()
    test_f_match_via_link()
    test_get_open_orders_filters_cancelled()
    test_collect_open_order_ids_sets_entry_sl_tp()
    test_g_placed_with_sl_matches_before_open()
    test_h_bridge_matches_placed_doc()
    test_i_pnl_candidates_include_placed()
    test_j_aave_closed_unverified_match_via_entry_price()
    test_k_entry_price_ambiguous_picks_newest_before_entry()
    test_l_entry_price_candidates_include_closed_unverified()
    print("OK — test_pnl_match_fallback")
