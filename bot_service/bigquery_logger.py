# Lokalizacja: bot_service/bigquery_logger.py
import json
import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
ANALYTICAL_TABLE_REF: Optional[bigquery.TableReference] = None
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


def initialize_bigquery() -> bool:
    global bigquery_client, ANALYTICAL_TABLE_REF, REAL_TRADES_TABLE_REF
    if bigquery_client is not None:
        logger.info("[BQ_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True

    try:
        logger.info("[BQ_INIT] Próba inicjalizacji klienta BigQuery...")

        DATASET_LOCATION = "US"
        client = bigquery.Client(location=DATASET_LOCATION)

        dataset_ref = client.dataset(constants.BIGQUERY_DATASET_ID)

        real_trades_table_id = constants.BIGQUERY_REAL_TRADES_TABLE_ID
        REAL_TRADES_TABLE_REF = dataset_ref.table(real_trades_table_id)
        client.get_table(REAL_TRADES_TABLE_REF)
        logger.info(f"[BQ_INIT] Pomyślnie zweryfikowano tabelę transakcji rzeczywistych: {real_trades_table_id}")

        analytical_table_id = constants.BIGQUERY_REAL_TRADES_TABLE_ID
        ANALYTICAL_TABLE_REF = dataset_ref.table(analytical_table_id)
        logger.info(f"[BQ_INIT] ANALYTICAL_TABLE_REF ustawiony na: {analytical_table_id}")

        bigquery_client = client
        logger.info(f"[BQ_INIT] Klient BigQuery pomyślnie zainicjalizowany. Lokalizacja: {DATASET_LOCATION}")
        return True

    except Exception as e:
        logger.critical(f"[BQ_INIT] KRYTYCZNY BŁĄD: Inicjalizacja klienta BigQuery nie powiodła się: {e}", exc_info=True)
        bigquery_client, ANALYTICAL_TABLE_REF, REAL_TRADES_TABLE_REF = None, None, None
        return False


def get_bigquery_client() -> bigquery.Client:
    if bigquery_client is None:
        logger.error("[BQ_CLIENT] Próba użycia niezainicjalizowanego klienta BigQuery.")
        raise RuntimeError("Klient BigQuery nie został pomyślnie zainicjalizowany.")
    return bigquery_client


def build_market_structure_signal_row(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Buduje wiersz insertu do market_structure_signals (używane też w smoke testach)."""
    raw_ctx = result_data.get("raw_context") or {}
    mf = result_data.get("market_features") or {}

    row: Dict[str, Any] = {
        "event_id": result_data.get("event_id"),
        "signal_id": result_data.get("signal_id"),
        "symbol": result_data.get("symbol"),
        "timestamp": result_data.get("timestamp"),
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
        "raw_context": json.dumps(raw_ctx),
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
        client = get_bigquery_client()
        table_ref = client.dataset("trading_analytics").table("market_structure_signals")
        row = build_market_structure_signal_row(result_data)
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"[BQ] market_structure_signals insert errors: {errors}")
        else:
            logger.info(f"[BQ] market_structure_signals OK event_id={event_id}")
    except Exception as e:
        logger.error(f"[BQ] market_structure_signals insert failed: {e}")
