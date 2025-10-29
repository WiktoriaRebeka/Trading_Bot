# Lokalizacja: shared_lib/constants.py

import os

FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LATEST_KLINES_COLLECTION = "latest_klines"
ANALYTICAL_CASES_COLLECTION = "analytical_cases"
ACTIVE_ORDERS_COLLECTION = "active_orders"
PROCESSED_ALERT_IDS_COLLECTION = "processed_alert_ids"
PROCESSED_ORDER_IDS_COLLECTION = "processed_pnl_ids"

# Nazwy dokumentów i pól
SYMBOLS_CONFIG_DOC_ID = "symbols_config"
LAST_PROCESSED_TS_FIELD = "last_processed_timestamp"

# === Konfiguracja BigQuery ===
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_ANALYTICAL_TABLE_ID = "new_trades_history"
BIGQUERY_REAL_TRADES_TABLE_ID = "real_trades_history"

# === Konfiguracja API ===
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"