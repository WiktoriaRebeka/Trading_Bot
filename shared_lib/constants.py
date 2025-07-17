# Lokalizacja: shared_lib/constants.py

import os

# --- Konfiguracja Projektu ---
GCP_PROJECT_ID = "trading-bot-463318"
BIGQUERY_PROJECT_ID = "trading-bot-463318"
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"

# --- Konfiguracja Firestore ---
FIRESTORE_DATABASE_ID = "trading-bot-data"
# Nazwy kolekcji
ALERTS_COLLECTION = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "open_trades"
ANALYZED_COLLECTION = "analyzed_trades"
LATEST_KLINES_COLLECTION = "latest_klines"
# Nazwy dokumentów
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
SYMBOLS_CONFIG_DOC_ID = "symbols_config"
# Nazwy pól
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
SYMBOLS_FIELD_NAME = "symbols_to_watch"

# --- Konfiguracja API Zewnętrznego ---
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"

# --- Zmienne ładowane z Sekretów ---
# Te pozostają zależne od os.getenv, ponieważ zostaną załadowane przez config_loader
# i odczytane przez obiekt AppConfig.
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")