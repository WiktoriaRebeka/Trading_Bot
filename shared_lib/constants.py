import os

# Nazwy kolekcji i dokumentów w Firestore
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
SYMBOLS_CONFIG_DOC_ID = "symbols_config"

SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "open_trades"
ANALYZED_COLLECTION = "analyzed_trades"
LATEST_KLINES_COLLECTION = "latest_klines"

# Adresy URL API Bybit - SKONFIGUROWANE DLA TESTNETU
BYBIT_API_URL_V5 = "https://api-testnet.bybit.com"
BYBIT_API_URL_V5_KLINE = "https://api-testnet.bybit.com/v5/market/kline"

# Konfiguracja BigQuery
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"