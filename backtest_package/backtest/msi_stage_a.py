#!/usr/bin/env python3
"""
Etap A — kalibracja struktury MSI OrderBlock (offline).

KROK 1: publiczne kline M1 Bybit → parquet
ETAP A: MsiEngine replay vs BigQuery OB_NEW (tylko SELECT)

NIE zapisuje do BigQuery / Firestore / Bybit.
NIE składa zleceń. NIE restartuje bota.

Kolejne etapy (NIE implementowane tutaj):
  - karencja 30 min + failed-break
  - Limit GTC, SL/TP 2R + fee, filtr wysokości 0.2%
  - supersede_mode = "live" | "keep_all_limits"
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from orderflow_engine.config_symbols import ALL_SYMBOLS_FOR_WS
from orderflow_engine.msi_engine import MsiCandle, MsiEngine, OrderBlock, StructureEvent

logger = logging.getLogger("msi_stage_a")

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BQ_PROJECT = "trading-bot-463318"
BQ_TABLE = "`trading-bot-463318.trading_analytics.orderblock_events`"
DEFAULT_START = "2026-07-15T00:00:00Z"
DEFAULT_COMPARE_START = "2026-08-15T15:59:00Z"

# Placeholder kolejnych etapów — nie używane w Etapie A.
SUPERSEDE_MODE_PLACEHOLDER = "live"  # "live" | "keep_all_limits"
COOLDOWN_MINUTES_PLACEHOLDER = 30
MIN_OB_HEIGHT_PCT_PLACEHOLDER = 0.2
RR_PLACEHOLDER = 2.0

# tickSize z instrument_rules (Firestore, zrzut użytkownika) — tylko do tolerancji 1 tick.
TICK_SIZE: Dict[str, float] = {
    "AAVEUSDT": 0.01,
    "ADAUSDT": 0.0001,
    "ALGOUSDT": 0.0001,
    "APTUSDT": 0.001,
    "ARBUSDT": 0.0001,
    "ATOMUSDT": 0.001,
    "AVAXUSDT": 0.001,
    "AXSUSDT": 0.001,
    "BCHUSDT": 0.1,
    "BNBUSDT": 0.1,
    "BTCUSDT": 0.1,
    "COMPUSDT": 0.01,
    "CROUSDT": 0.00001,
    "CRVUSDT": 0.0001,
    "DOGEUSDT": 0.00001,
    "DOTUSDT": 0.0001,
    "EIGENUSDT": 0.0001,
    "ENAUSDT": 0.0001,
    "ENSUSDT": 0.001,
    "ETCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "FLRUSDT": 0.00001,
    "GRASSUSDT": 0.0001,
    "GRTUSDT": 0.00001,
    "HBARUSDT": 0.00001,
    "HYPEUSDT": 0.001,
    "ICPUSDT": 0.001,
    "IMXUSDT": 0.0001,
    "INJUSDT": 0.001,
    "JASMYUSDT": 0.000001,
    "JTOUSDT": 0.0001,
    "KASUSDT": 0.00001,
    "LDOUSDT": 0.0001,
    "LINKUSDT": 0.001,
    "LTCUSDT": 0.1,
    "MNTUSDT": 0.0001,
    "NEARUSDT": 0.001,
    "ONDOUSDT": 0.0001,
    "OPUSDT": 0.00001,
    "ORDIUSDT": 0.001,
    "PENDLEUSDT": 0.001,
    "POLUSDT": 0.0001,
    "PYTHUSDT": 0.0001,
    "RENDERUSDT": 0.001,
    "RUNEUSDT": 0.0001,
    "SANDUSDT": 0.0001,
    "SEIUSDT": 0.00001,
    "SOLUSDT": 0.01,
    "STRKUSDT": 0.000001,
    "STXUSDT": 0.0001,
    "SUIUSDT": 0.0001,
    "TAOUSDT": 0.001,
    "THETAUSDT": 0.0001,
    "TIAUSDT": 0.001,
    "TONUSDT": 0.0001,
    "TRXUSDT": 0.00001,
    "UNIUSDT": 0.001,
    "VIRTUALUSDT": 0.0001,
    "WIFUSDT": 0.0001,
    "WLDUSDT": 0.0001,
    "XLMUSDT": 0.00001,
    "XMRUSDT": 0.01,
    "XRPUSDT": 0.0001,
    "XTZUSDT": 0.0001,
    "ZECUSDT": 0.01,
    "ZROUSDT": 0.001,
}

RATE_LIMIT_RETCODES = {10006, 10018}
BACKOFF_SEC = (1.0, 2.0, 4.0, 8.0, 16.0, 16.0)


class RateLimiter:
    def __init__(self, max_rps: float) -> None:
        self._min_interval = (1.0 / max_rps) if max_rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_ok - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_ok = now + self._min_interval


def parse_iso_utc(text: str) -> datetime:
    raw = text.strip().replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ms_to_iso(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_closed_minute_open_ms(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    current_open = (dt_to_ms(now) // 60_000) * 60_000
    return current_open - 60_000


def tick_size_for(symbol: str) -> float:
    return float(TICK_SIZE.get(symbol.upper(), 1e-8))


def prices_within_one_tick(a: float, b: float, tick: float) -> bool:
    return abs(float(a) - float(b)) <= (tick * 1.01)


def unique_ob_key(symbol: str, candle_ts_ms: int, direction: str, high: float, low: float) -> Tuple:
    tick = tick_size_for(symbol)
    hi_n = round(float(high) / tick)
    lo_n = round(float(low) / tick)
    return (symbol.upper(), int(candle_ts_ms), str(direction).upper(), hi_n, lo_n)


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def dt_any_to_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt_to_ms(dt.astimezone(timezone.utc))
    if isinstance(value, (int, float)):
        n = int(value)
        if n < 10**12:
            n *= 1000
        return n
    if isinstance(value, str):
        return dt_to_ms(parse_iso_utc(value))
    return None


def ensure_dirs(root: Path) -> Tuple[Path, Path]:
    parquet_dir = root / "data" / "bybit_m1_parquet"
    reports_dir = root / "data" / "reports"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    return parquet_dir, reports_dir


def bybit_get_klines(symbol: str, end_ms: int, limiter: RateLimiter, timeout_sec: float = 30.0) -> dict:
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "1",
        "end": str(int(end_ms)),
        "limit": "1000",
    }
    url = f"{BYBIT_KLINE_URL}?{urllib.parse.urlencode(params)}"
    last_err: Optional[Exception] = None
    for attempt in range(len(BACKOFF_SEC) + 1):
        limiter.acquire()
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "msi-stage-a-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            ret = payload.get("retCode")
            if ret in RATE_LIMIT_RETCODES:
                raise RuntimeError(f"bybit retCode={ret} {payload.get('retMsg')}")
            if ret != 0:
                raise RuntimeError(f"bybit retCode={ret} {payload.get('retMsg')}")
            return payload
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (429, 403) and attempt == 0 and e.code < 500:
                raise
            if attempt >= len(BACKOFF_SEC):
                raise
            backoff = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
            logger.warning("%s HTTP %s — retry za %.0fs (%s/%s)", symbol, e.code, backoff, attempt + 1, len(BACKOFF_SEC))
            time.sleep(backoff)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            rate_hit = "10006" in str(e) or "429" in str(e) or "too many" in msg or "rate" in msg
            if not rate_hit and attempt >= 2:
                raise
            if attempt >= len(BACKOFF_SEC):
                raise
            backoff = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
            logger.warning("%s fetch error %s — retry za %.0fs", symbol, type(e).__name__, backoff)
            time.sleep(backoff)
    raise RuntimeError(f"{symbol} kline fetch failed: {last_err}")


def fetch_symbol_m1(
    symbol: str,
    start_ms: int,
    end_ms: int,
    limiter: RateLimiter,
) -> List[dict]:
    by_ts: Dict[int, dict] = {}
    cursor_end = int(end_ms)
    pages = 0
    while True:
        payload = bybit_get_klines(symbol, cursor_end, limiter)
        raw_list = (payload.get("result") or {}).get("list") or []
        pages += 1
        if not raw_list:
            break
        oldest: Optional[int] = None
        for k in raw_list:
            ts = int(k[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if ts < start_ms or ts > end_ms:
                continue
            by_ts[ts] = {
                "ts": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        if oldest is None:
            break
        if oldest <= start_ms:
            break
        if len(raw_list) < 1000:
            break
        cursor_end = oldest - 60_000
        if pages > 500:
            raise RuntimeError(f"{symbol} zbyt wiele stron kline ({pages})")
    rows = [by_ts[k] for k in sorted(by_ts)]
    return rows


def count_gaps(timestamps: Sequence[int]) -> int:
    if len(timestamps) < 2:
        return 0
    missing = 0
    for a, b in zip(timestamps, timestamps[1:]):
        delta = int(b) - int(a)
        if delta > 60_000:
            missing += (delta // 60_000) - 1
    return missing


def write_parquet(path: Path, rows: List[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "ts": [int(r["ts"]) for r in rows],
            "open": [float(r["open"]) for r in rows],
            "high": [float(r["high"]) for r in rows],
            "low": [float(r["low"]) for r in rows],
            "close": [float(r["close"]) for r in rows],
            "volume": [float(r["volume"]) for r in rows],
        }
    )
    pq.write_table(table, path)


def read_parquet(path: Path) -> List[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    n = len(next(iter(cols.values()), []))
    return [
        {
            "ts": int(cols["ts"][i]),
            "open": float(cols["open"][i]),
            "high": float(cols["high"][i]),
            "low": float(cols["low"][i]),
            "close": float(cols["close"][i]),
            "volume": float(cols["volume"][i]),
        }
        for i in range(n)
    ]


def parquet_path(parquet_dir: Path, symbol: str) -> Path:
    return parquet_dir / f"symbol={symbol}.parquet"


def download_all_symbols(
    symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
    parquet_dir: Path,
    reports_dir: Path,
    workers: int,
    refresh: bool,
    max_rps: float,
) -> List[dict]:
    limiter = RateLimiter(max_rps)
    report_rows: List[dict] = []
    lock = threading.Lock()

    def one(sym: str) -> dict:
        out = parquet_path(parquet_dir, sym)
        rec: Dict[str, Any] = {
            "symbol": sym,
            "first_ts": "",
            "first_iso": "",
            "last_ts": "",
            "last_iso": "",
            "n_candles": 0,
            "n_gaps": 0,
            "error": "",
            "path": str(out),
        }
        try:
            if out.exists() and not refresh:
                rows = read_parquet(out)
                logger.info("%s skip download — parquet istnieje (%s świec)", sym, len(rows))
            else:
                logger.info("%s pobieranie M1 %s → %s", sym, ms_to_iso(start_ms), ms_to_iso(end_ms))
                rows = fetch_symbol_m1(sym, start_ms, end_ms, limiter)
                write_parquet(out, rows)
            if rows:
                rec["first_ts"] = rows[0]["ts"]
                rec["first_iso"] = ms_to_iso(rows[0]["ts"])
                rec["last_ts"] = rows[-1]["ts"]
                rec["last_iso"] = ms_to_iso(rows[-1]["ts"])
                rec["n_candles"] = len(rows)
                rec["n_gaps"] = count_gaps([r["ts"] for r in rows])
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            logger.error("%s BŁĄD: %s", sym, rec["error"])
        with lock:
            report_rows.append(rec)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(one, s) for s in symbols]
        for fut in as_completed(futs):
            fut.result()

    report_rows.sort(key=lambda r: r["symbol"])
    csv_path = reports_dir / "m1_data_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["symbol", "first_ts", "first_iso", "last_ts", "last_iso", "n_candles", "n_gaps", "error", "path"],
        )
        w.writeheader()
        w.writerows(report_rows)
    logger.info("Zapisano %s", csv_path)
    return report_rows


def replay_ob_new(symbol: str, rows: List[dict], compare_start_ms: int) -> List[dict]:
    collected: List[dict] = []

    def sink(ev: StructureEvent, ob: Optional[OrderBlock]) -> None:
        if ev.event_type != "OB_NEW" or ob is None:
            return
        if int(ev.ts) < compare_start_ms:
            return
        collected.append(
            {
                "symbol": symbol,
                "ob_candle_ts": int(ob.candle.ts),
                "ob_direction": str(ob.direction).upper(),
                "ob_high": float(ob.ob_high),
                "ob_low": float(ob.ob_low),
                "event_ts": int(ev.ts),
                "chain_id": ob.chain_id,
                "detected_at_ts": int(ob.detected_at_ts),
            }
        )

    engine = MsiEngine(symbol=symbol, on_event=sink)
    for row in rows:
        engine.on_candle_close(MsiCandle.from_dict(row))
    return collected


def load_live_ob_new(compare_start: datetime, bq_location: str) -> List[dict]:
    from google.cloud import bigquery

    sql = f"""
    SELECT
      symbol,
      ob_candle_ts,
      ob_direction,
      ob_high,
      ob_low,
      event_ts,
      chain_id
    FROM {BQ_TABLE}
    WHERE event_type = 'OB_NEW'
      AND event_ts >= @compare_start
    """
    client = bigquery.Client(project=BQ_PROJECT)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("compare_start", "TIMESTAMP", compare_start),
        ]
    )
    logger.info("BigQuery SELECT OB_NEW od %s (location=%s)", compare_start.isoformat(), bq_location)
    job = client.query(sql, job_config=job_config, location=bq_location)
    rows: List[dict] = []
    for r in job.result():
        rows.append(
            {
                "symbol": str(r["symbol"] or "").upper().replace(".P", ""),
                "ob_candle_ts": dt_any_to_ms(r["ob_candle_ts"]),
                "ob_direction": str(r["ob_direction"] or "").upper(),
                "ob_high": to_float(r["ob_high"]),
                "ob_low": to_float(r["ob_low"]),
                "event_ts": dt_any_to_ms(r["event_ts"]),
                "chain_id": r["chain_id"],
            }
        )
    logger.info("BigQuery zwróciło %s wierszy OB_NEW (przed dedup)", len(rows))
    return rows


def dedup_obs(rows: Iterable[dict]) -> List[dict]:
    seen: Dict[Tuple, dict] = {}
    for r in rows:
        if r.get("ob_candle_ts") is None or r.get("ob_high") is None or r.get("ob_low") is None:
            continue
        if not r.get("symbol") or not r.get("ob_direction"):
            continue
        key = unique_ob_key(r["symbol"], int(r["ob_candle_ts"]), r["ob_direction"], float(r["ob_high"]), float(r["ob_low"]))
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def match_obs(live: List[dict], backtest: List[dict]) -> Tuple[List[Tuple[dict, dict]], List[dict], List[dict]]:
    used_live = set()
    matched: List[Tuple[dict, dict]] = []
    groups: Dict[Tuple[str, int, str], List[int]] = {}
    for i, lv in enumerate(live):
        g = (lv["symbol"], int(lv["ob_candle_ts"]), lv["ob_direction"])
        groups.setdefault(g, []).append(i)

    for bt in backtest:
        g = (bt["symbol"], int(bt["ob_candle_ts"]), bt["ob_direction"])
        tick = tick_size_for(bt["symbol"])
        best_i: Optional[int] = None
        best_dist = None
        for i in groups.get(g, []):
            if i in used_live:
                continue
            lv = live[i]
            if not prices_within_one_tick(bt["ob_high"], lv["ob_high"], tick):
                continue
            if not prices_within_one_tick(bt["ob_low"], lv["ob_low"], tick):
                continue
            dist = abs(float(bt["ob_high"]) - float(lv["ob_high"])) + abs(float(bt["ob_low"]) - float(lv["ob_low"]))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_i = i
        if best_i is not None:
            used_live.add(best_i)
            matched.append((live[best_i], bt))

    only_live = [lv for i, lv in enumerate(live) if i not in used_live]
    matched_bt = {id(bt) for _, bt in matched}
    only_bt = [bt for bt in backtest if id(bt) not in matched_bt]
    return matched, only_live, only_bt


def write_ob_csv(path: Path, rows: List[dict]) -> None:
    fields = ["symbol", "ob_candle_ts", "ob_candle_iso", "ob_direction", "ob_high", "ob_low", "event_ts", "event_iso", "chain_id"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "symbol": r.get("symbol"),
                    "ob_candle_ts": r.get("ob_candle_ts"),
                    "ob_candle_iso": ms_to_iso(r.get("ob_candle_ts")),
                    "ob_direction": r.get("ob_direction"),
                    "ob_high": r.get("ob_high"),
                    "ob_low": r.get("ob_low"),
                    "event_ts": r.get("event_ts"),
                    "event_iso": ms_to_iso(r.get("event_ts")),
                    "chain_id": r.get("chain_id"),
                }
            )


def run_stage_a(
    symbols: Sequence[str],
    parquet_dir: Path,
    reports_dir: Path,
    compare_start: datetime,
    bq_location: str,
) -> None:
    compare_ms = dt_to_ms(compare_start)
    backtest_all: List[dict] = []
    for sym in symbols:
        path = parquet_path(parquet_dir, sym)
        if not path.exists():
            logger.warning("%s brak parquet — pomijam replay", sym)
            continue
        rows = read_parquet(path)
        logger.info("%s replay MSI (%s świec), porównanie od %s", sym, len(rows), compare_start.isoformat())
        backtest_all.extend(replay_ob_new(sym, rows, compare_ms))

    live_raw = load_live_ob_new(compare_start, bq_location)
    watch = {s.upper() for s in symbols}
    live_raw = [r for r in live_raw if r["symbol"] in watch]
    live = dedup_obs(live_raw)
    backtest = dedup_obs(backtest_all)
    matched, only_live, only_bt = match_obs(live, backtest)

    summary = {
        "compare_start": compare_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_rows_raw": len(live_raw),
        "live_unique": len(live),
        "backtest_unique": len(backtest),
        "matched": len(matched),
        "only_live": len(only_live),
        "only_backtest": len(only_bt),
        "bq_query": "SELECT-only from trading_analytics.orderblock_events",
    }
    json_path = reports_dir / "ob_new_comparison.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Podsumowanie: %s", summary)

    per_symbol: Dict[str, Dict[str, int]] = {}
    for s in symbols:
        per_symbol[s] = {"live_unique": 0, "backtest_unique": 0, "matched": 0, "only_live": 0, "only_backtest": 0}
    for r in live:
        per_symbol.setdefault(r["symbol"], {"live_unique": 0, "backtest_unique": 0, "matched": 0, "only_live": 0, "only_backtest": 0})
        per_symbol[r["symbol"]]["live_unique"] += 1
    for r in backtest:
        per_symbol.setdefault(r["symbol"], {"live_unique": 0, "backtest_unique": 0, "matched": 0, "only_live": 0, "only_backtest": 0})
        per_symbol[r["symbol"]]["backtest_unique"] += 1
    for lv, _bt in matched:
        per_symbol[lv["symbol"]]["matched"] += 1
    for r in only_live:
        per_symbol[r["symbol"]]["only_live"] += 1
    for r in only_bt:
        per_symbol[r["symbol"]]["only_backtest"] += 1

    per_path = reports_dir / "ob_new_comparison_per_symbol.csv"
    with per_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "live_unique", "backtest_unique", "matched", "only_live", "only_backtest"])
        w.writeheader()
        for s in sorted(per_symbol):
            row = {"symbol": s, **per_symbol[s]}
            w.writerow(row)

    write_ob_csv(reports_dir / "ob_new_only_live.csv", only_live)
    write_ob_csv(reports_dir / "ob_new_only_backtest.csv", only_bt)
    logger.info("Raporty Etapu A w %s", reports_dir)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MSI Etap A: dane M1 + kalibracja OB_NEW vs BigQuery (tylko SELECT)")
    p.add_argument("--start", default=DEFAULT_START, help="Początek świec M1 UTC (ISO), default 2026-07-15T00:00:00Z")
    p.add_argument("--compare-start", default=DEFAULT_COMPARE_START, help="Początek okna porównania OB_NEW UTC")
    p.add_argument("--workers", type=int, default=4, help="Równoległość pobierania kline")
    p.add_argument("--max-rps", type=float, default=3.0, help="Globalny limit zapytań Bybit kline / s")
    p.add_argument("--refresh-data", action="store_true", help="Pobierz kline ponownie mimo istniejącego parquet")
    p.add_argument("--data-only", action="store_true", help="Tylko KROK 1 (bez BigQuery i replay)")
    p.add_argument("--compare-only", action="store_true", help="Tylko Etap A (wymaga parquet)")
    p.add_argument("--bq-location", default="EU", help="Lokalizacja joba BigQuery (EU/US)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = parse_iso_utc(args.start)
    compare_start = parse_iso_utc(args.compare_start)
    start_ms = dt_to_ms(start)
    end_ms = last_closed_minute_open_ms()
    symbols = [s.upper().replace(".P", "") for s in ALL_SYMBOLS_FOR_WS]
    parquet_dir, reports_dir = ensure_dirs(PACKAGE_ROOT)

    logger.info(
        "Symbole=%s start=%s compare_start=%s end(last closed)=%s workers=%s",
        len(symbols),
        start.isoformat(),
        compare_start.isoformat(),
        ms_to_iso(end_ms),
        args.workers,
    )
    logger.info("Zapis wyłącznie lokalny pod %s — brak INSERT/UPDATE BigQuery/Firestore/Bybit", PACKAGE_ROOT / "data")

    if not args.compare_only:
        download_all_symbols(
            symbols,
            start_ms,
            end_ms,
            parquet_dir,
            reports_dir,
            workers=max(1, int(args.workers)),
            refresh=bool(args.refresh_data),
            max_rps=float(args.max_rps),
        )
    if args.data_only:
        return 0

    run_stage_a(symbols, parquet_dir, reports_dir, compare_start, args.bq_location)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
