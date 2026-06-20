#!/usr/bin/env python3
"""
Dry-run / apply: weryfikuje active_orders (OPEN + ERROR) wobec Bybit.

OPEN + Bybit size=0  → would_close (widmo)
OPEN + Bybit size>0  → would_keep  (NIE ruszać przy --apply)
ERROR                → would_close (odrzucone zlecenie, bez sprawdzania Bybit)

Domyślnie --dry-run (zero zapisów). --apply zamyka TYLKO would_close.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared_lib.firebase_client import initialize_firebase
from shared_lib.secret_manager import get_secret
from bot_service.bybit_executor import BybitExecutor
from bot_service import state_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

THROTTLE_SEC = float(os.environ.get("CLEANUP_BYBIT_THROTTLE_SEC", "0.15"))
CLOSED_STATUS = "CLOSED_RECONCILED"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _position_size(pos: Optional[Dict[str, Any]]) -> float:
    if not pos:
        return 0.0
    return _safe_float(pos.get("size"))


def _position_side(pos: Dict[str, Any]) -> Optional[str]:
    side = pos.get("side")
    if side == "Buy":
        return "LONG"
    if side == "Sell":
        return "SHORT"
    return str(side) if side else None


def _normalize_symbol(raw: Any) -> str:
    return str(raw or "").upper().replace(".P", "")


def _build_executor() -> BybitExecutor:
    project_id = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("Ustaw GCP_PROJECT lub GOOGLE_CLOUD_PROJECT.")

    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    api_key = get_secret("bybit-api-key", project_id)
    api_secret = get_secret("bybit-api-secret", project_id)
    if not api_key or not api_secret:
        raise RuntimeError("Nie udało się pobrać bybit-api-key / bybit-api-secret z Secret Manager.")

    return BybitExecutor(api_key=api_key, api_secret=api_secret, testnet=use_testnet)


def _filter_docs(docs: List[Any], symbol_filter: Optional[str], limit: Optional[int]) -> List[Any]:
    if symbol_filter:
        sym = _normalize_symbol(symbol_filter)
        docs = [d for d in docs if _normalize_symbol((d.to_dict() or {}).get("symbol")) == sym]
    if limit is not None:
        docs = docs[:limit]
    return docs


def _group_open_by_symbol(docs: List[Any]) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    grouped: Dict[str, List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for doc in docs:
        data = doc.to_dict() or {}
        symbol = _normalize_symbol(data.get("symbol"))
        if not symbol:
            continue
        grouped[symbol].append((doc.id, data))
    return grouped


def _close_payload(reason: str, source: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": CLOSED_STATUS,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "closed_reason": reason,
        "reconciled_by": source,
    }
    if extra:
        payload.update(extra)
    return payload


def _apply_closures(rows: List[Dict[str, Any]], source: str) -> int:
    applied = 0
    for row in rows:
        doc_id = row["event_id"]
        reason = row.get("close_reason", "unknown")
        extra = {}
        if row.get("error"):
            extra["error"] = row["error"]
        state_manager.update_active_order(doc_id, _close_payload(reason, source, extra or None))
        applied += 1
        logger.info("CLOSED %s | %s | reason=%s", doc_id, row.get("symbol"), reason)
    return applied


def run_cleanup(
    *,
    dry_run: bool,
    symbol_filter: Optional[str],
    limit: Optional[int],
) -> Dict[str, Any]:
    if not initialize_firebase():
        raise RuntimeError("Inicjalizacja Firestore nie powiodła się.")

    executor = _build_executor()
    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"

    open_docs = _filter_docs(list(state_manager.get_orders_by_status("OPEN")), symbol_filter, limit)
    error_docs = _filter_docs(list(state_manager.get_orders_by_status("ERROR")), symbol_filter, limit)
    grouped_open = _group_open_by_symbol(open_docs)

    would_close: List[Dict[str, Any]] = []
    would_keep: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []
    symbol_checks: Dict[str, Dict[str, Any]] = {}

    for doc_id, data in [(d.id, d.to_dict() or {}) for d in error_docs]:
        would_close.append({
            "event_id": doc_id,
            "symbol": _normalize_symbol(data.get("symbol")),
            "direction": data.get("direction"),
            "created_at": data.get("created_at"),
            "previous_status": "ERROR",
            "close_reason": "rejected_order_error_status",
            "error": data.get("error"),
            "bybit_size": None,
        })

    for idx, (symbol, entries) in enumerate(sorted(grouped_open.items())):
        if idx > 0:
            time.sleep(THROTTLE_SEC)

        try:
            pos = executor.get_position_info(symbol)
            size = _position_size(pos)
            has_position = size > 0
            symbol_checks[symbol] = {
                "bybit_size": size,
                "bybit_side": _position_side(pos) if pos and has_position else None,
                "open_doc_count": len(entries),
            }
        except Exception as exc:
            logger.error("Bybit API error for %s: %s", symbol, exc, exc_info=True)
            api_errors.append({"symbol": symbol, "error": str(exc), "open_doc_count": len(entries)})
            symbol_checks[symbol] = {"error": str(exc), "open_doc_count": len(entries)}
            continue

        for doc_id, data in entries:
            row = {
                "event_id": doc_id,
                "symbol": symbol,
                "direction": data.get("direction"),
                "created_at": data.get("created_at"),
                "trailing_stop_set": data.get("trailing_stop_set"),
                "previous_status": "OPEN",
                "bybit_size": size,
            }
            if has_position:
                would_keep.append(row)
            else:
                row["close_reason"] = "ghost_open_no_bybit_position"
                would_close.append(row)

    would_close_open = sum(1 for r in would_close if r.get("previous_status") == "OPEN")
    would_close_error = sum(1 for r in would_close if r.get("previous_status") == "ERROR")

    report: Dict[str, Any] = {
        "mode": "dry-run" if dry_run else "apply",
        "use_testnet": use_testnet,
        "total_open_docs": len(open_docs),
        "total_error_docs": len(error_docs),
        "unique_symbols": len(grouped_open),
        "would_close": len(would_close),
        "would_close_open_ghosts": would_close_open,
        "would_close_error": would_close_error,
        "would_keep": len(would_keep),
        "api_errors": len(api_errors),
        "would_close_docs": would_close,
        "would_keep_docs": would_keep,
        "api_error_details": api_errors,
        "symbol_checks": symbol_checks,
        "applied_closures": 0,
    }

    if not dry_run:
        if api_errors:
            raise RuntimeError(
                f"Przerwano --apply: {len(api_errors)} błędów API Bybit — "
                "napraw i uruchom ponownie, żeby nie zamknąć żywych pozycji przez pomyłkę."
            )
        report["applied_closures"] = _apply_closures(would_close, "cleanup_ghost_open_orders.py")

    return report


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("GHOST OPEN ORDERS — CLEANUP REPORT")
    print("=" * 60)
    print(f"mode:                  {report['mode']}")
    print(f"use_testnet:           {report['use_testnet']}")
    print(f"total_open_docs:       {report['total_open_docs']}")
    print(f"total_error_docs:      {report['total_error_docs']}")
    print(f"unique_symbols:        {report['unique_symbols']}")
    print(f"would_close:           {report['would_close']}  (śmieci — zamknąć)")
    print(f"  - open ghosts:       {report['would_close_open_ghosts']}  (OPEN, Bybit size=0)")
    print(f"  - error rejects:     {report['would_close_error']}  (status=ERROR)")
    print(f"would_keep:            {report['would_keep']}  (REALNE — NIE RUSZAĆ)")
    print(f"api_errors:            {report['api_errors']}")
    if report["mode"] == "apply":
        print(f"applied_closures:      {report['applied_closures']}")
    print("=" * 60)

    if report["would_keep_docs"]:
        print("\n--- would_keep (REALNE pozycje, size>0) ---")
        for row in report["would_keep_docs"]:
            print(
                f"  {row['event_id']} | {row['symbol']} {row.get('direction')} "
                f"| bybit_size={row['bybit_size']}"
            )

    if report["api_error_details"]:
        print("\n--- api_errors (symbole pominięte — NIE zamknięte) ---")
        for err in report["api_error_details"]:
            print(f"  {err['symbol']}: {err['error']} ({err['open_doc_count']} OPEN docs)")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup ghost OPEN/ERROR orders in Firestore vs Bybit.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Zamknij would_close w Firestore (po akceptacji dry-run).",
    )
    parser.add_argument("--symbol", type=str, default=None, help="Tylko jeden symbol, np. BTCUSDT")
    parser.add_argument("--limit", type=int, default=None, help="Max dokumentów per status do skanowania")
    parser.add_argument("--json-out", type=str, default=None, help="Ścieżka do pełnego raportu JSON")
    args = parser.parse_args()

    dry_run = not args.apply

    try:
        report = run_cleanup(
            dry_run=dry_run,
            symbol_filter=args.symbol,
            limit=args.limit,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "credentials" in msg.lower() or "Firestore" in msg:
            logger.error(
                "Brak dostępu do GCP. Ustaw GOOGLE_APPLICATION_CREDENTIALS "
                "lub uruchom: gcloud auth application-default login"
            )
        logger.error("Cleanup failed: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Cleanup failed: %s", exc, exc_info=True)
        return 1

    _print_summary(report)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Pełny raport JSON: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
