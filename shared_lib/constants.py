# Lokalizacja: shared_lib/constants.py
import os

GCP_PROJECT_ID = "trading-bot-463318"
BIGQUERY_PROJECT_ID = GCP_PROJECT_ID

# Reszta stałych bez zmian
FIRESTORE_COLLECTION_ALERTS = "alerts"
# ... (skopiuj resztę stałych z Twojego istniejącego pliku)
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
SYMBOLS_CONFIG_DOC_ID = "symbols_config"
SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "open_trades"
ANALYZED_COLLECTION = "analyzed_trades"
LATEST_KLINES_COLLECTION = "latest_klines"
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"

# Te zmienne będą ładowane z Secret Managera przez config_loader
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")