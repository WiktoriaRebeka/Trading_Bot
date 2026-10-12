#!/usr/bin/env python3
"""
ETAP 3a — sprzątanie duchów OPEN → CLOSED_STALE (bez kasowania).

Domyślnie --dry-run (zero zapisów). --apply po akceptacji audytu.
Tylko signal_mode=msi_orderblock, status=OPEN, Bybit size=0.
NIE uruchamiaj --apply na produkcji bez zgody.
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
CLOSED_STATUS = "CLOSED_STALE"
MSI_MODE = "msi_orderblock"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
        raise RuntimeError("Nie udało się pobrać bybit-api-key / bybit-api-secret.")
    return BybitExecutor(api_key=api_key, api_secret=api_secret, testnet=use_testnet)


def _msi_open_docs(symbol_filter: Optional[str], limit: Optional[int]) -> List[Any]:
    docs = []
    for doc in state_manager.get_orders_by_status("OPEN"):
        data = doc.to_dict() or {}
        if str(data.get("signal_mode", "")).lower() != MSI_MODE:
            continue
        if symbol_filter:
            if _normalize_symbol(data.get("symbol")) != _normalize_symbol(symbol_filter):
                continue
        docs.append(doc)
        if limit is not None and len(docs) >= limit:
            break
    return docs


def run_cleanup(*, dry_run: bool, symbol_filter: Optional[str], limit: Optional[int]) -> Dict[str, Any]:
    if not initialize_firebase():
        raise RuntimeError("Inicjalizacja Firestore nie powiodła się.")

    executor = _build_executor()
    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    open_docs = _msi_open_docs(symbol_filter, limit)

    by_symbol: Dict[str, List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for doc in open_docs:
        data = doc.to_dict() or {}
        symbol = _normalize_symbol(data.get("symbol"))
        if not symbol:
            continue
        by_symbol[symbol].append((doc.id, data))

    would_stale: List[Dict[str, Any]] = []
    would_keep: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []

    for idx, (symbol, entries) in enumerate(sorted(by_symbol.items())):
        if idx > 0:
            time.sleep(THROTTLE_SEC)
        try:
            pos = executor.get_position_info(symbol)
            size = _safe_float(pos.get("size") if pos else None)
            has_position = size > 0
        except Exception as exc:
            logger.error("Bybit API error for %s: %s", symbol, exc, exc_info=True)
            api_errors.append({"symbol": symbol, "error": str(exc), "open_count": len(entries)})
            continue

        for doc_id, data in entries:
            row = {
                "event_id": doc_id,
                "symbol": symbol,
                "direction": data.get("direction"),
                "created_at": data.get("created_at"),
                "bybit_size": size,
            }
            if has_position:
                would_keep.append(row)
            else:
                row["close_reason"] = "ghost_open_no_bybit_position"
                would_stale.append(row)

    report: Dict[str, Any] = {
        "mode": "dry-run" if dry_run else "apply",
        "use_testnet": use_testnet,
        "open_msi_count": len(open_docs),
        "would_stale": len(would_stale),
        "would_keep": len(would_keep),
        "api_errors": len(api_errors),
        "would_stale_docs": would_stale,
        "would_keep_docs": would_keep,
        "api_error_details": api_errors,
        "ghost_symbols": sorted({r["symbol"] for r in would_stale}),
        "applied": 0,
    }

    if not dry_run:
        if api_errors:
            raise RuntimeError(
                f"Przerwano --apply: {len(api_errors)} błędów API Bybit — "
                "nie oznaczaj duchów przy niepewności pozycji."
            )
        now = datetime.now(timezone.utc).isoformat()
        for row in would_stale:
            state_manager.update_active_order(
                row["event_id"],
                {
                    "status": CLOSED_STATUS,
                    "closed_at": now,
                    "closed_reason": row.get("close_reason", "ghost_open_no_bybit_position"),
                    "reconciled_by": "cleanup_stale_open_orders.py",
                },
            )
            logger.info(
                "CLOSED_STALE %s | %s %s",
                row["event_id"],
                row["symbol"],
                row.get("direction"),
            )
            report["applied"] += 1

    return report


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("CLEANUP STALE OPEN → CLOSED_STALE")
    print("=" * 60)
    print(f"mode:            {report['mode']}")
    print(f"use_testnet:     {report['use_testnet']}")
    print(f"OPEN (MSI):      {report['open_msi_count']}")
    print(f"would_stale:     {report['would_stale']}  (duchy → CLOSED_STALE)")
    print(f"would_keep:      {report['would_keep']}  (żywe — NIE ruszać)")
    print(f"api_errors:      {report['api_errors']}")
    if report["mode"] == "apply":
        print(f"applied:         {report['applied']}")
    print(f"symbole duchów:  {', '.join(report['ghost_symbols']) or '(brak)'}")
    print("=" * 60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Oznacz duchy MSI OPEN jako CLOSED_STALE (domyślnie dry-run)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Zapisz CLOSED_STALE w Firestore (wymaga świadomej zgody).",
    )
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", type=str, default=None)
    args = parser.parse_args()

    try:
        report = run_cleanup(
            dry_run=not args.apply,
            symbol_filter=args.symbol,
            limit=args.limit,
        )
    except Exception as exc:
        logger.error("Cleanup failed: %s", exc, exc_info=True)
        return 1

    _print_summary(report)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
