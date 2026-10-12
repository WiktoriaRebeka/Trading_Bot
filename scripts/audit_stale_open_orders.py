#!/usr/bin/env python3
"""
ETAP 1 — audyt duchów OPEN (tylko odczyt).

Skanuje active_orders ze statusem OPEN i signal_mode=msi_orderblock,
dla każdego symbolu sprawdza pozycję na Bybit i wypisuje:
  - liczba OPEN (MSI)
  - liczba duchów (OPEN bez pozycji na Bybit)
  - lista symboli duchów

ZERO zapisów do Firestore / Bybit.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from collections import defaultdict
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

THROTTLE_SEC = float(os.environ.get("AUDIT_BYBIT_THROTTLE_SEC", "0.15"))
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


def _msi_open_docs() -> List[Any]:
    docs = []
    for doc in state_manager.get_orders_by_status("OPEN"):
        data = doc.to_dict() or {}
        if str(data.get("signal_mode", "")).lower() != MSI_MODE:
            continue
        docs.append(doc)
    return docs


def run_audit() -> Dict[str, Any]:
    if not initialize_firebase():
        raise RuntimeError("Inicjalizacja Firestore nie powiodła się.")

    executor = _build_executor()
    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    open_docs = _msi_open_docs()

    by_symbol: Dict[str, List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for doc in open_docs:
        data = doc.to_dict() or {}
        symbol = _normalize_symbol(data.get("symbol"))
        if not symbol:
            continue
        by_symbol[symbol].append((doc.id, data))

    ghosts: List[Dict[str, Any]] = []
    live: List[Dict[str, Any]] = []
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
                live.append(row)
            else:
                ghosts.append(row)

    ghost_symbols = sorted({g["symbol"] for g in ghosts})
    return {
        "use_testnet": use_testnet,
        "open_msi_count": len(open_docs),
        "unique_symbols": len(by_symbol),
        "ghost_count": len(ghosts),
        "live_count": len(live),
        "api_errors": len(api_errors),
        "ghost_symbols": ghost_symbols,
        "ghosts": ghosts,
        "live": live,
        "api_error_details": api_errors,
    }


def _print_report(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("AUDIT STALE OPEN (MSI) — READ ONLY")
    print("=" * 60)
    print(f"use_testnet:     {report['use_testnet']}")
    print(f"OPEN (MSI):      {report['open_msi_count']}")
    print(f"unique_symbols:  {report['unique_symbols']}")
    print(f"duchy (OPEN bez pozycji Bybit): {report['ghost_count']}")
    print(f"live (OPEN + pozycja Bybit):    {report['live_count']}")
    print(f"api_errors:      {report['api_errors']}")
    print("-" * 60)
    print(f"symbole duchów ({len(report['ghost_symbols'])}):")
    if report["ghost_symbols"]:
        print("  " + ", ".join(report["ghost_symbols"]))
    else:
        print("  (brak)")
    if report["ghosts"]:
        print("\n--- szczegóły duchów ---")
        for row in report["ghosts"]:
            print(
                f"  {row['event_id']} | {row['symbol']} {row.get('direction')} "
                f"| created_at={row.get('created_at')}"
            )
    if report["api_error_details"]:
        print("\n--- api_errors ---")
        for err in report["api_error_details"]:
            print(f"  {err['symbol']}: {err['error']}")
    print("=" * 60 + "\n")


def main() -> int:
    try:
        report = run_audit()
    except Exception as exc:
        logger.error("Audit failed: %s", exc, exc_info=True)
        return 1
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
