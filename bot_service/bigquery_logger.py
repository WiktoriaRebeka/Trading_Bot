# Lokalizacja: bot_service/bigquery_logger.py
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from google.cloud import bigquery
from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
SIGNALS_TABLE_REF: Optional[bigquery.TableReference] = None
REAL_TRADES_TABLE_REF: Optional[bigquery.TableReference] = None

MARKET_FEATURE_COLUMNS = (
    "obi_value", "bid_volume_top10", "ask_volume_top10",
    "spread", "best_bid", "best_ask",
    "real_wall_detected", "real_wall_price", "real_wall_size", "real_wall_distance_pct",
    "dom_check_passed",
    "delta_last", "buy_volume_300s", "sell_volume_300s",
    "delta_velocity", "cvd", "trade_count_300s",
    "delta_divergence_real", "delta_divergence_strength",
    "liq_volume_total", "matched_liq_volume",
    "liq_event_count", "last_liq_side", "last_liq_age_s",
    "funding_rate", "open_interest", "volume_24h",
    "swing_strength", "sweep_depth_pct", "distance_to_swing_pct",
    "tp_was_capped", "fallback_sl_used",
    "confidence_score",
)

REAL_TRADES_COLUMNS = (
    "event_id", "alert_id", "order_id", "signal_id", "symbol", "direction",
    "qty", "leverage", "avg_entry_price", "avg_exit_price",
    "entry_value_usdt", "exit_value_usdt", "gross_pnl_usdt", "commission_usdt",
    "net_pnl_usdt", "exit_type", "exit_price_result",
    "timestamp_signal", "timestamp_entry", "timestamp_close",
    "planned_risk_usdt", "realized_r", "realized_rrr",
    "alert_entry_price", "alert_sl_price", "alert_tp_price",
    "planned_entry_price", "planned_sl_price", "planned_2r_price",
    "entry_slippage_pct", "trailing_activated", "trailing_active_price", "session",
)


def initialize_bigquery() -> bool:
    global bigquery_client, SIGNALS_TABLE_REF, REAL_TRADES_TABLE_REF
    if bigquery_client is not None:
        logger.info("[BQ_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True

    try:
        logger.info("[BQ_INIT] Próba inicjalizacji klienta BigQuery...")

        DATASET_LOCATION = "US"
        project = constants.BIGQUERY_PROJECT_ID
        client = bigquery.Client(project=project, location=DATASET_LOCATION)
        dataset_ref = client.dataset(constants.BIGQUERY_DATASET_ID)

        signals_table_id = constants.BIGQUERY_SIGNALS_TABLE_ID
        SIGNALS_TABLE_REF = dataset_ref.table(signals_table_id)
        client.get_table(SIGNALS_TABLE_REF)
        logger.info(f"[BQ_INIT] Zweryfikowano tabelę sygnałów: {signals_table_id}")

        real_trades_table_id = constants.BIGQUERY_REAL_TRADES_TABLE_ID
        REAL_TRADES_TABLE_REF = dataset_ref.table(real_trades_table_id)
        client.get_table(REAL_TRADES_TABLE_REF)
        logger.info(f"[BQ_INIT] Zweryfikowano tabelę transakcji: {real_trades_table_id}")

        bigquery_client = client
        logger.info(f"[BQ_INIT] Klient BigQuery OK. Projekt={project}, lokalizacja={DATASET_LOCATION}")
        return True

    except Exception as e:
        logger.critical(f"[BQ_INIT] KRYTYCZNY BŁĄD: Inicjalizacja klienta BigQuery nie powiodła się: {e}", exc_info=True)
        bigquery_client, SIGNALS_TABLE_REF, REAL_TRADES_TABLE_REF = None, None, None
        return False


def get_bigquery_client() -> bigquery.Client:
    if bigquery_client is None:
        logger.error("[BQ_CLIENT] Próba użycia niezainicjalizowanego klienta BigQuery.")
        raise RuntimeError("Klient BigQuery nie został pomyślnie zainicjalizowany.")
    return bigquery_client


def _normalize_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _sanitize_bq_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, datetime):
        return _normalize_timestamp(value)
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_bq_value(v) for v in value]
    return str(value)


def filter_row_columns(row: Dict[str, Any], columns: tuple) -> Dict[str, Any]:
    """Tylko kolumny ze schematu tabeli — unika błędów insert po zmianie schematu BQ."""
    return {col: _sanitize_bq_value(row.get(col)) for col in columns}


def build_market_structure_signal_row(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Buduje wiersz insertu do market_structure_signals (używane też w smoke testach)."""
    raw_ctx = result_data.get("raw_context") or {}
    mf = result_data.get("market_features") or {}

    row: Dict[str, Any] = {
        "event_id": result_data.get("event_id"),
        "signal_id": result_data.get("signal_id"),
        "symbol": result_data.get("symbol"),
        "timestamp": _normalize_timestamp(result_data.get("timestamp")),
        "direction": result_data.get("direction"),
        "entry": result_data.get("entry"),
        "sl": result_data.get("sl"),
        "tp": result_data.get("tp"),
        "risk_pct": result_data.get("risk_pct"),
        "rr": result_data.get("rr"),
        "risk_usdt": result_data.get("risk_usdt"),
        "structure_state": result_data.get("structure_state"),
        "session": result_data.get("session"),
        "minute_of_day": result_data.get("minute_of_day"),
        "day_of_week": result_data.get("day_of_week"),
        "raw_context": raw_ctx if isinstance(raw_ctx, dict) else {},
    }
    for col in MARKET_FEATURE_COLUMNS:
        row[col] = mf.get(col)
    return row


def log_analysis_result(result_data: Dict[str, Any]):
    event_id = result_data.get("event_id", "unknown")
    mf = result_data.get("market_features") or {}
    logger.info(
        f"[SIGNAL_ANALYTICS][{event_id}] "
        f"symbol={result_data.get('symbol')} "
        f"direction={result_data.get('direction')} "
        f"entry={result_data.get('entry')} "
        f"sl={result_data.get('sl')} "
        f"tp={result_data.get('tp')} "
        f"matched_liq={mf.get('matched_liq_volume')} "
        f"funding_rate={mf.get('funding_rate')} "
        f"real_wall={mf.get('real_wall_detected')} "
        f"session={result_data.get('session')} "
        f"minute_of_day={result_data.get('minute_of_day')}"
    )
    try:
        if not initialize_bigquery():
            raise RuntimeError("BigQuery nie zainicjalizowane")
        client = get_bigquery_client()
        if SIGNALS_TABLE_REF is None:
            raise RuntimeError("SIGNALS_TABLE_REF nie ustawiony")
        row = build_market_structure_signal_row(result_data)
        errors = client.insert_rows_json(SIGNALS_TABLE_REF, [row])
        if errors:
            logger.error(f"[BQ] market_structure_signals insert errors: {errors}")
        else:
            logger.info(f"[BQ] market_structure_signals OK event_id={event_id}")
    except Exception as e:
        logger.error(f"[BQ] market_structure_signals insert failed: {e}", exc_info=True)


def insert_real_trade_row(row: Dict[str, Any]) -> List[Any]:
    """Insert do real_trades_history — tylko kolumny ze schematu."""
    if not initialize_bigquery():
        raise RuntimeError("BigQuery nie zainicjalizowane")
    client = get_bigquery_client()
    if REAL_TRADES_TABLE_REF is None:
        raise RuntimeError("REAL_TRADES_TABLE_REF nie ustawiony")
    filtered = filter_row_columns(row, REAL_TRADES_COLUMNS)
    return client.insert_rows_json(REAL_TRADES_TABLE_REF, [filtered])
