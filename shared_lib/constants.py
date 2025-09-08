# Lokalizacja: shared_lib/constants.py

import os

# === Konfiguracja Firestore ===
# Nazwy kolekcji
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LATEST_KLINES_COLLECTION = "latest_klines"
ANALYTICAL_CASES_COLLECTION = "analytical_cases"
ACTIVE_ORDERS_COLLECTION = "active_orders"  # <-- Dodana stała dla aktywnych zleceń

# Nazwy dokumentów i pól
SYMBOLS_CONFIG_DOC_ID = "symbols_config"
LAST_PROCESSED_TS_FIELD = "last_processed_timestamp"

# === Konfiguracja BigQuery ===
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
# Tabela dla analizy historycznej (backtestu)
BIGQUERY_ANALYTICAL_TABLE_ID = "new_trades_history"
# Tabela dla wyników realnego handlu
BIGQUERY_REAL_TRADES_TABLE_ID = "real_trades_history" # <-- Dodana stała dla realnych transakcji

# === Konfiguracja API ===
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"