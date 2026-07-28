#!/usr/bin/env python3
"""Read-only diagnostic: active_orders collection size and breakdown."""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared_lib.firebase_client import initialize_firebase, get_db


def _parse_created_at(raw) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def main() -> None:
    if not initialize_firebase():
        print("ERROR: initialize_firebase() failed")
        sys.exit(1)

    db = get_db()
    now = datetime.now(timezone.utc)
    thresholds = {
        "older_than_7d": now - timedelta(days=7),
        "older_than_14d": now - timedelta(days=14),
        "older_than_30d": now - timedelta(days=30),
    }

    status_counts: Counter = Counter()
    age_buckets = {k: 0 for k in thresholds}
    no_created_at = 0
    msi_orderblock = 0
    total = 0

    print("=== active_orders diagnostic (read-only) ===")
    print(f"timestamp_utc: {now.isoformat()}")
    print()

    for doc in db.collection("active_orders").stream():
        total += 1
        data = doc.to_dict() or {}

        status = data.get("status")
        status_key = "<missing>" if status is None else str(status)
        status_counts[status_key] += 1

        if str(data.get("signal_mode", "")).lower() == "msi_orderblock":
            msi_orderblock += 1

        created = _parse_created_at(data.get("created_at"))
        if created is None:
            no_created_at += 1
            continue
        for label, cutoff in thresholds.items():
            if created < cutoff:
                age_buckets[label] += 1

    print(f"1. total_documents: {total}")
    print()
    print("2. by_status:")
    for status, count in sorted(status_counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"   {status}: {count}")
    print()
    print("3. by_age (created_at):")
    print(f"   no_created_at: {no_created_at}")
    for label in ("older_than_7d", "older_than_14d", "older_than_30d"):
        print(f"   {label}: {age_buckets[label]}")
    print()
    print("4. signal_mode == msi_orderblock:")
    print(f"   count: {msi_orderblock}")
    print()
    print("=== end ===")


if __name__ == "__main__":
    main()
