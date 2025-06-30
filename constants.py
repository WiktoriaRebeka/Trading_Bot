# W pliku: /constants.py
# ZASTĄP CAŁĄ ZAWARTOŚĆ PLIKU

import os

# Ten plik teraz tylko odczytuje zmienne środowiskowe.
# Ich ładowanie odbywa się w config_loader.py na starcie aplikacji.

# --- Firestore ---
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
SYMBOLS_CONFIG_DOC_ID = "symbols_config"

# Architektura Wielo-Kolekcyjna
SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "open_trades"
ANALYZED_COLLECTION = "analyzed_trades"
LATEST_KLINES_COLLECTION = "latest_klines"

# --- Bybit API ---
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"
# Klucze API będą teraz pobierane ze zmiennych środowiskowych,
# które zostały załadowane przez config_loader.
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# --- Konfiguracja Analityki BigQuery ---
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"