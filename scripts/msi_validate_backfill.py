#!/usr/bin/env python3
"""
Walidacja offline silnika MSI — replay świec M1 z Bybit, eksport wykrytych OB.

Użycie:
  cd Trading_Bot
  python scripts/msi_validate_backfill.py --symbol BTCUSDT --limit 2000
  python scripts/msi_validate_backfill.py --symbol BTCUSDT --limit 500 --output ob_report.csv --print-events
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orderflow_engine.backfiller import HistoryBackfiller
from orderflow_engine.msi_engine import MsiCandle, MsiEngine, OrderBlock, StructureEvent
from orderflow_engine.msi_event_logger import MsiEventLogger

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _ts_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


async def fetch_m1(symbol: str, limit: int) -> list[dict]:
    async with HistoryBackfiller() as bf:
        result = await bf.fetch_history(symbol, interval="1", limit=limit)
        if not result.ok:
            raise RuntimeError(f"Backfill failed for {symbol}")
        return result.rows


def run_replay(symbol: str, candles: list[dict], print_events: bool) -> tuple[list[OrderBlock], list[StructureEvent]]:
    events: list[StructureEvent] = []
    msi_logger = MsiEventLogger()
    msi_logger.log_to_bq = False

    def sink(ev: StructureEvent, ob: OrderBlock | None) -> None:
        events.append(ev)
        msi_logger.handle_event(ev, ob)
        if print_events:
            print(f"  [{ev.event_type}] ts={_ts_iso(ev.ts)} {json.dumps(ev.payload, default=str)}")

    engine = MsiEngine(symbol=symbol, on_event=sink)
    for row in candles:
        engine.on_candle_close(MsiCandle.from_dict(row))
    return engine.orderblocks, events


def write_csv(path: Path, obs: list[OrderBlock], symbol: str) -> None:
    fields = [
        "symbol",
        "detected_at_ts",
        "detected_at_iso",
        "ob_candle_ts",
        "ob_candle_iso",
        "direction",
        "ob_high",
        "ob_low",
        "ob_height",
        "chain_id",
        "initial_trend",
        "hl_lh_level",
        "bos_level",
        "liquidity_level",
        "is_first",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for ob in obs:
            w.writerow(
                {
                    "symbol": symbol,
                    "detected_at_ts": ob.detected_at_ts,
                    "detected_at_iso": _ts_iso(ob.detected_at_ts),
                    "ob_candle_ts": ob.candle.ts,
                    "ob_candle_iso": _ts_iso(ob.candle.ts),
                    "direction": ob.direction,
                    "ob_high": ob.ob_high,
                    "ob_low": ob.ob_low,
                    "ob_height": ob.ob_height,
                    "chain_id": ob.chain_id,
                    "initial_trend": ob.initial_trend,
                    "hl_lh_level": ob.hl_lh_level,
                    "bos_level": ob.bos_level,
                    "liquidity_level": ob.liquidity_level,
                    "is_first": ob.is_first,
                }
            )


def main() -> None:
    p = argparse.ArgumentParser(description="MSI engine offline validation (M1 backfill)")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--output", default="msi_ob_report.csv")
    p.add_argument("--print-events", action="store_true", help="Pełna ścieżka eventów strukturalnych")
    args = p.parse_args()

    sym = args.symbol.upper().replace(".P", "")
    logger.info("Pobieranie %s świec M1 dla %s...", args.limit, sym)
    rows = asyncio.run(fetch_m1(sym, args.limit))
    logger.info("Otrzymano %s świec. Replay MSI...", len(rows))

    if args.print_events:
        print("--- EVENTY STRUKTURALNE ---")

    obs, events = run_replay(sym, rows, args.print_events)

    out = Path(args.output)
    write_csv(out, obs, sym)

    ob_events = [e for e in events if e.event_type == "OB_NEW"]
    logger.info("Wykryto %s OrderBlocków → zapisano %s", len(obs), out.resolve())
    logger.info(
        "Porównaj na wykresie 1M: kolumna detected_at_iso = minuta zamknięcia świecy oznaczającej OB; "
        "strefa = [ob_low, ob_high]."
    )
    if obs:
        last = obs[-1]
        logger.info(
            "Ostatni OB: %s %s [%s – %s] chain=%s",
            _ts_iso(last.detected_at_ts),
            last.direction,
            last.ob_low,
            last.ob_high,
            last.chain_id,
        )
    logger.info("Eventy łącznie: %s (OB_NEW=%s)", len(events), len(ob_events))


if __name__ == "__main__":
    main()
