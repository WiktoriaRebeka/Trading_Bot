#!/usr/bin/env python3
"""
Testy updater MSI → CLOSED_STALE:
- potwierdzony flat (API ok, size==0) + OPEN ≥10 min → CLOSED_STALE
- błąd API / niepełna odpowiedź → bez zmian
- OPEN młodsze niż 10 min → bez CLOSED_STALE
- żywa pozycja → backfill IDs
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


def _opened_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _run_open_cycle(
    open_docs,
    *,
    pos_check: Optional[Dict[str, Any]] = None,
    pos_check_exc: Optional[BaseException] = None,
    id_fields=None,
    footprint_pos=None,
):
    executor = MagicMock()
    updates: List[tuple] = []
    logs: List[tuple] = []

    def _log(level, stage, msg, **fields):
        logs.append((level, stage, msg, fields))

    def _update(oid, payload):
        updates.append((oid, dict(payload)))

    def _by_status(status):
        if status == "OPEN":
            return open_docs
        return []

    def _call_retry(fn, *a, **k):
        return fn(*a, **k)

    with patch.object(bl.state_manager, "get_orders_by_status", side_effect=_by_status), patch.object(
        bl.state_manager, "get_orders_without_status", return_value=[]
    ), patch.object(bl.state_manager, "update_active_order", side_effect=_update), patch.object(
        bl, "log_struct", side_effect=_log
    ), patch.object(
        bl, "call_with_retry", side_effect=_call_retry
    ), patch.object(
        bl, "_collect_open_order_ids", return_value=id_fields or {}
    ), patch(
        "bot_service.msi_order_handler.process_msi_cooldown_queue", MagicMock()
    ), patch(
        "bot_service.msi_order_handler.process_msi_deferred_queue", MagicMock()
    ):
        if pos_check_exc is not None:
            executor.check_linear_position.side_effect = pos_check_exc
        else:
            executor.check_linear_position.return_value = pos_check
        # footprint path nadal używa get_position_info
        executor.get_position_info.return_value = footprint_pos
        bl.update_filled_orders(executor)

    return updates, logs


def test_msi_open_ghost_marked_closed_stale():
    doc = _FakeDoc(
        "ghost-1",
        {
            "symbol": "GRASSUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
            "position_opened_at": _opened_ago(15),
        },
    )
    updates, logs = _run_open_cycle(
        [doc],
        pos_check={"ok": True, "size": 0.0, "position": None},
    )

    stale = [u for u in updates if u[0] == "ghost-1" and u[1].get("status") == "CLOSED_STALE"]
    assert len(stale) == 1
    assert stale[0][1].get("closed_reason") == "no_bybit_position"
    assert any(
        m[0] == "info" and m[2] == "[MSI] CLOSED_STALE" and m[3].get("event_id") == "ghost-1"
        for m in logs
    )


def test_msi_api_error_does_not_mark_stale():
    doc = _FakeDoc(
        "ghost-api",
        {
            "symbol": "KASUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
            "position_opened_at": _opened_ago(60),
        },
    )
    updates, logs = _run_open_cycle(
        [doc],
        pos_check={"ok": False, "error": "timeout", "size": None, "position": None},
    )
    assert not any(u[1].get("status") == "CLOSED_STALE" for u in updates)
    assert any("inconclusive" in m[2] for m in logs)


def test_msi_api_exception_does_not_mark_stale():
    doc = _FakeDoc(
        "ghost-exc",
        {
            "symbol": "ENAUSDT",
            "direction": "SHORT",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
            "position_opened_at": _opened_ago(60),
        },
    )
    updates, logs = _run_open_cycle(
        [doc],
        pos_check_exc=RuntimeError("bybit down"),
    )
    assert not any(u[1].get("status") == "CLOSED_STALE" for u in updates)
    assert any("position check failed" in m[2] for m in logs)


def test_msi_young_open_not_stale_even_if_flat():
    doc = _FakeDoc(
        "young-1",
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
            "position_opened_at": _opened_ago(3),
        },
    )
    updates, logs = _run_open_cycle(
        [doc],
        pos_check={"ok": True, "size": 0.0, "position": None},
    )
    assert not any(u[1].get("status") == "CLOSED_STALE" for u in updates)
    assert any("not eligible for CLOSED_STALE" in m[2] for m in logs)


def test_msi_missing_opened_at_not_stale():
    doc = _FakeDoc(
        "no-ts",
        {
            "symbol": "XMRUSDT",
            "direction": "SHORT",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
        },
    )
    updates, _logs = _run_open_cycle(
        [doc],
        pos_check={"ok": True, "size": 0.0, "position": None},
    )
    assert not any(u[1].get("status") == "CLOSED_STALE" for u in updates)


def test_msi_open_age_eligible_helpers():
    ok, reason = bl._msi_open_age_eligible(
        {"position_opened_at": _opened_ago(11)},
    )
    assert ok and reason is None
    ok2, reason2 = bl._msi_open_age_eligible(
        {"position_opened_at": _opened_ago(2)},
    )
    assert not ok2 and reason2 == "open_younger_than_min_age"


def test_msi_open_with_position_backfills_ids():
    doc = _FakeDoc(
        "live-1",
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "signal_mode": "msi_orderblock",
            "planned_sl_price": 90.0,
            "planned_tp_price": 120.0,
            "position_opened_at": _opened_ago(20),
        },
    )
    id_fields = {"entryOrderId": "e1", "slOrderId": "s1", "tpOrderId": "t1"}
    updates, logs = _run_open_cycle(
        [doc],
        pos_check={
            "ok": True,
            "size": 1.0,
            "position": {"size": "1", "side": "Buy", "avgPrice": "100"},
        },
        id_fields=id_fields,
    )

    assert not any(u[1].get("status") == "CLOSED_STALE" for u in updates)
    bf = [u for u in updates if u[0] == "live-1" and u[1].get("slOrderId") == "s1"]
    assert bf and bf[0][1]["tpOrderId"] == "t1"
    assert bf[0][1]["entryOrderId"] == "e1"
    assert any("Backfill entry/sl/tp OrderId" in m[2] for m in logs)


def test_footprint_open_still_closed_reconciled():
    """Footprint (nie MSI) nadal → CLOSED_RECONCILED przy braku pozycji."""
    doc = _FakeDoc(
        "fp-1",
        {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "signal_mode": "footprint",
            "trailing_stop_set": True,
        },
    )
    updates, logs = _run_open_cycle(
        [doc],
        pos_check={"ok": True, "size": 0.0, "position": None},
        footprint_pos=None,
    )
    rec = [u for u in updates if u[0] == "fp-1" and u[1].get("status") == "CLOSED_RECONCILED"]
    assert len(rec) == 1
    assert any(m[2] == "Order marked CLOSED_RECONCILED" for m in logs)


def test_check_linear_position_ok_flat_vs_error():
    from bot_service.bybit_executor import BybitExecutor

    ex = BybitExecutor(api_key="k", api_secret="s", testnet=True)
    with patch.object(ex, "_send_request", return_value={"list": []}):
        out = ex.check_linear_position("BTCUSDT")
    assert out["ok"] is True and out["size"] == 0.0

    with patch.object(ex, "_send_request", return_value={"retCode": 0}):
        out2 = ex.check_linear_position("BTCUSDT")
    assert out2["ok"] is False

    from bot_service.bybit_executor import BybitAPIError

    with patch.object(ex, "_send_request", side_effect=BybitAPIError(10001, "fail")):
        out3 = ex.check_linear_position("BTCUSDT")
    assert out3["ok"] is False and out3["error"]


if __name__ == "__main__":
    test_msi_open_age_eligible_helpers()
    test_msi_open_ghost_marked_closed_stale()
    test_msi_api_error_does_not_mark_stale()
    test_msi_api_exception_does_not_mark_stale()
    test_msi_young_open_not_stale_even_if_flat()
    test_msi_missing_opened_at_not_stale()
    test_msi_open_with_position_backfills_ids()
    test_footprint_open_still_closed_reconciled()
    test_check_linear_position_ok_flat_vs_error()
    print("OK — test_msi_stale_open_updater")
