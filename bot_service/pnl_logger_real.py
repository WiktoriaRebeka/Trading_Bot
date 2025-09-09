# Lokalizacja: bot_service/pnl_logger_real.py
import logging
from typing import Dict, Any
from datetime import datetime, timezone
from google.cloud import bigquery
from decimal import Decimal

from bot_service.bigquery_logger import get_bigquery_client
from shared_lib import constants

logger = logging.getLogger(__name__)

# Definicja referencji do tabeli jest pobierana z centralnego miejsca
REAL_TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_REAL_TRADES_TABLE_ID}"

# ======================================================================================
# === OSTATECZNA, ROZBUDOWANA SCHEMA DLA PEŁNEJ ANALIZY TRANSAKCJI ===
# ======================================================================================
REAL_TRADES_HISTORY_SCHEMA = [
    # --- Identyfikatory ---
    bigquery.SchemaField("alert_id", "STRING", mode="REQUIRED", description="ID alertu z naszego systemu, który wygenerował transakcję."),
    bigquery.SchemaField("order_id", "STRING", mode="REQUIRED", description="ID zlecenia wejścia zwrócone przez giełdę."),
    
    # --- Parametry Transakcji ---
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("direction", "STRING", mode="REQUIRED", description="Kierunek transakcji: LONG lub SHORT."),
    bigquery.SchemaField("qty", "NUMERIC", mode="REQUIRED", description="Wielkość pozycji w jednostkach kryptowaluty (np. 0.1 BTC)."),
    bigquery.SchemaField("leverage", "INTEGER", mode="NULLABLE", description="Użyta dźwignia."),
    
    # --- Ceny ---
    bigquery.SchemaField("avg_entry_price", "NUMERIC", mode="REQUIRED", description="Rzeczywista, średnia cena wejścia."),
    bigquery.SchemaField("avg_exit_price", "NUMERIC", mode="REQUIRED", description="Rzeczywista, średnia cena wyjścia."),
    
    # --- Wartości Pozycji (Obliczone) ---
    bigquery.SchemaField("entry_value_usdt", "NUMERIC", mode="REQUIRED", description="Wartość pozycji w USDT w momencie wejścia (qty * avg_entry_price)."),
    bigquery.SchemaField("exit_value_usdt", "NUMERIC", mode="REQUIRED", description="Wartość pozycji w USDT w momencie wyjścia (qty * avg_exit_price)."),

    # --- Wyniki Finansowe ---
    bigquery.SchemaField("gross_pnl_usdt", "NUMERIC", mode="REQUIRED", description="Zysk/strata brutto w USDT, przed prowizjami (exit_value - entry_value)."),
    bigquery.SchemaField("commission_usdt", "NUMERIC", mode="REQUIRED", description="Łączna prowizja zapłacona za otwarcie i zamknięcie pozycji."),
    bigquery.SchemaField("net_pnl_usdt", "NUMERIC", mode="REQUIRED", description="Zysk/strata netto w USDT, po odjęciu prowizji (oficjalna wartość z Bybit)."),

    # --- Metadane ---
    bigquery.SchemaField("exit_type", "STRING", mode="NULLABLE", description="Powód zamknięcia pozycji (np. TakeProfit, StopLoss, Manual)."),
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED", description="Timestamp otwarcia pozycji."),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED", description="Timestamp zamknięcia pozycji."),
]

def log_real_trade_result(enriched_pnl_data: Dict[str, Any]):
    """Transformuje wzbogacone dane PnL z Bybit, oblicza dodatkowe metryki i zapisuje je do BigQuery."""
    
    try:
        # Używamy Decimal do precyzyjnych obliczeń finansowych
        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(enriched_pnl_data.get("avgExitPrice", "0.0"))
        
        # Obliczamy wymagane wartości
        entry_value = qty * avg_entry_price
        exit_value = qty * avg_exit_price
        
        # Dla pozycji SHORT, PnL brutto to (wartość wejścia - wartość wyjścia)
        direction = "LONG" if enriched_pnl_data.get("side") == "Buy" else "SHORT"
        if direction == "SHORT":
            gross_pnl = entry_value - exit_value
        else: # LONG
            gross_pnl = exit_value - entry_value

        transformed_data = {
            "alert_id": enriched_pnl_data.get("alert_id", "unknown"),
            "order_id": enriched_pnl_data.get("orderId", "unknown"),
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": direction,
            "qty": float(qty),
            "leverage": int(float(enriched_pnl_data.get("leverage", 1))),
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(avg_exit_price),
            "entry_value_usdt": float(entry_value),
            "exit_value_usdt": float(exit_value),
            "gross_pnl_usdt": float(gross_pnl),
            "commission_usdt": float(Decimal(enriched_pnl_data.get("cumCommission", "0.0"))),
            "net_pnl_usdt": float(Decimal(enriched_pnl_data.get("closedPnl", "0.0"))),
            "exit_type": enriched_pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Błąd podczas transformacji danych PnL: {e}", exc_info=True, extra={"json_fields": {"pnl_data": enriched_pnl_data}})
        return

    logger.info(f"Logowanie realnego wyniku dla {transformed_data['symbol']} (Alert ID: {transformed_data['alert_id']}) do BigQuery.")
    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if errors:
            logger.error(f"Błąd podczas wstawiania realnych wyników do BigQuery: {errors}")
        else:
            logger.info("Pomyślnie zapisano realny wynik transakcji.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu realnych wyników: {e}", exc_info=True)