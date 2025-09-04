# Lokalizacja: shared_lib/constants.py

import os

# Nazwy kolekcji i dokumentów w Firestore
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
SYMBOLS_CONFIG_DOC_ID = "symbols_config"
LATEST_KLINES_COLLECTION = "latest_klines"

# NOWA, GŁÓWNA KOLEKCJA ROBOCZA
ANALYTICAL_CASES_COLLECTION = "analytical_cases"

# Adresy URL API
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"

# Klucze API (USUNIĘTE - teraz dostępne tylko przez obiekt config)
# BYBIT_API_KEY = os.getenv("BYBIT_API_KEY") <--- USUNIĘTE
# BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET") <--- USUNIĘTE

# Konfiguracja BigQuery
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "new_trades_history" # Zaktualizowana nazwa tabeli