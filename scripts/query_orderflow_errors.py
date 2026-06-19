#!/usr/bin/env python3
"""Query Cloud Logging for orderflow-engine errors after deploy."""
from datetime import datetime, timezone, timedelta

from google.cloud import logging as cloud_logging

PROJECT = "trading-bot-463318"
SERVICE = "orderflow-engine"
DAYS = 7


def main():
    client = cloud_logging.Client(project=PROJECT)
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    cutoff_s = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    filters = [
        (
            "ERRORS + Error evaluating",
            f'resource.type="cloud_run_revision" '
            f'resource.labels.service_name="{SERVICE}" '
            f'timestamp>="{cutoff_s}" '
            f'(severity>=ERROR OR textPayload:"Error evaluating" OR '
            f'textPayload:"_build_market_features" OR textPayload:"Traceback")',
        ),
        (
            "FILTER skips (info)",
            f'resource.type="cloud_run_revision" '
            f'resource.labels.service_name="{SERVICE}" '
            f'timestamp>="{cutoff_s}" '
            f'textPayload:"SKIPPED" OR textPayload:"FAILED"',
        ),
        (
            "Alert sent",
            f'resource.type="cloud_run_revision" '
            f'resource.labels.service_name="{SERVICE}" '
            f'timestamp>="{cutoff_s}" '
            f'(textPayload:"send_alert" OR textPayload:"ALERT" OR textPayload:"SYGNAŁ")',
        ),
    ]

    for title, flt in filters:
        print(f"\n{'='*60}\n{title}\n{'='*60}")
        try:
            entries = list(
                client.list_entries(filter_=flt, order_by=cloud_logging.DESCENDING, max_results=40)
            )
        except Exception as exc:
            print(f"QUERY FAILED: {exc}")
            continue
        print(f"Count: {len(entries)}")
        for e in entries[:25]:
            ts = e.timestamp
            sev = e.severity
            if isinstance(e.payload, dict):
                msg = e.payload.get("message") or e.payload.get("textPayload") or str(e.payload)
            else:
                msg = str(e.payload)
            print(f"\n--- {ts} [{sev}] ---")
            print(msg[:3000])


if __name__ == "__main__":
    main()
