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
# Należy zastąpić starą definicję REAL_TRADES_HISTORY_SCHEMA tą nową

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
    bigquery.SchemaField("gross_pnl_usdt", "NUMERIC", mode="REQUIRED", description="Zysk/strata brutto w USDT, przed prowizjami (net_pnl_usdt + commission_usdt)."),
    bigquery.SchemaField("commission_usdt", "NUMERIC", mode="REQUIRED", description="Łączna prowizja zapłacona za otwarcie i zamknięcie pozycji."),
    bigquery.SchemaField("net_pnl_usdt", "NUMERIC", mode="REQUIRED", description="Zysk/strata netto w USDT, po odjęciu prowizji (oficjalna wartość z Bybit)."),

    # --- Metadane ---
    bigquery.SchemaField("exit_type", "STRING", mode="NULLABLE", description="Powód zamknięcia pozycji (np. TakeProfit, StopLoss, Manual)."),
    bigquery.SchemaField("timestamp_entry", "TIMESTAMP", mode="REQUIRED", description="Timestamp otwarcia pozycji."),
    bigquery.SchemaField("timestamp_close", "TIMESTAMP", mode="REQUIRED", description="Timestamp zamknięcia pozycji."),

    # --- NOWE POLA: Analiza Ryzyka i R:R ---
    bigquery.SchemaField("sl_price", "NUMERIC", mode="NULLABLE", description="Planowana cena Stop Loss z oryginalnego alertu."),
    bigquery.SchemaField("planned_risk_usdt", "NUMERIC", mode="NULLABLE", description="Rzeczywiste ryzyko w USDT, obliczone na podstawie avg_entry_price i sl_price."),
    bigquery.SchemaField("realized_rrr", "NUMERIC", mode="NULLABLE", description="Rzeczywisty, zrealizowany stosunek ryzyka do zysku (net_pnl_usdt / planned_risk_usdt)."),
]

def log_real_trade_result(enriched_pnl_data: Dict[str, Any]):
    """Transformuje wzbogacone dane PnL z Bybit, wzbogaca je o dane z alertu, oblicza R:R i zapisuje do BigQuery."""
    
    alert_id = enriched_pnl_data.get("alert_id", "unknown")
    
    original_alert_data = state_manager.get_alert_data_by_id(alert_id)
    if not original_alert_data:
        logger.error(f"Nie można obliczyć R:R, ponieważ nie znaleziono oryginalnego alertu o ID: {alert_id}")
        sl_price_from_alert = Decimal("0.0")
    else:
        sl_price_from_alert = Decimal(str(original_alert_data.get("sl", "0.0")))

    try:
        side = enriched_pnl_data.get("side")
        if side == "Buy":
            direction = "LONG"
        elif side == "Sell":
            direction = "SHORT"
        else:
            direction = "UNKNOWN"

        qty = Decimal(enriched_pnl_data.get("qty", "0.0"))
        avg_entry_price = Decimal(enriched_pnl_data.get("avgEntryPrice", "0.0"))
        avg_exit_price = Decimal(enriched_pnl_data.get("avgExitPrice", "0.0"))
        commission = Decimal(enriched_pnl_data.get("cumCommission") or "0.0")
        net_pnl = Decimal(enriched_pnl_data.get("closedPnl") or "0.0")

        planned_risk_usdt = Decimal("0.0")
        realized_rrr = Decimal("0.0")

        if sl_price_from_alert > 0 and avg_entry_price > 0:
            risk_per_unit = abs(avg_entry_price - sl_price_from_alert)
            planned_risk_usdt = risk_per_unit * qty
            
            if planned_risk_usdt > 0:
                realized_rrr = net_pnl / planned_risk_usdt
        
        # === KLUCZOWA POPRAWKA: Zaokrąglanie wartości przed konwersją na float ===
        # Zaokrąglamy do 6 miejsc po przecinku, co jest bezpieczną i wystarczającą precyzją dla BigQuery.
        
        transformed_data = {
            "alert_id": alert_id,
            "order_id": enriched_pnl_data.get("orderId", "unknown"),
            "symbol": enriched_pnl_data.get("symbol"),
            "direction": direction,
            "qty": float(qty),
            "leverage": int(float(enriched_pnl_data.get("leverage", 1))),
            "avg_entry_price": float(avg_entry_price),
            "avg_exit_price": float(avg_exit_price),
            "entry_value_usdt": float(round(qty * avg_entry_price, 6)),
            "exit_value_usdt": float(round(qty * avg_exit_price, 6)),
            "gross_pnl_usdt": float(round(net_pnl + commission, 6)),
            "commission_usdt": float(commission),
            "net_pnl_usdt": float(net_pnl),
            "exit_type": enriched_pnl_data.get("exitType"),
            "timestamp_entry": datetime.fromtimestamp(int(enriched_pnl_data.get("createdTime")) / 1000, tz=timezone.utc).isoformat(),
            "timestamp_close": datetime.fromtimestamp(int(enriched_pnl_data.get("updatedTime")) / 1000, tz=timezone.utc).isoformat(),
            "sl_price": float(sl_price_from_alert),
            "planned_risk_usdt": float(round(planned_risk_usdt, 6)),
            "realized_rrr": float(round(realized_rrr, 6)) # <--- To jest bezpośrednia naprawa błędu
        }
    except Exception as e:
        logger.error(f"Błąd podczas transformacji danych PnL dla alertu {alert_id}: {e}", exc_info=True, extra={"json_fields": {"pnl_data": enriched_pnl_data}})
        return

    logger.info(f"Logowanie realnego wyniku dla {transformed_data['symbol']} (Alert ID: {alert_id}, R:R: {transformed_data['realized_rrr']:.2f}) do BigQuery.")
    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(REAL_TABLE_REF, [transformed_data])
        if errors:
            logger.error(f"Błąd podczas wstawiania realnych wyników do BigQuery: {errors}")
        else:
            logger.info("Pomyślnie zapisano realny wynik transakcji.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu realnych wyników: {e}", exc_info=True)