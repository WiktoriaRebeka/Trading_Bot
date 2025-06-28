# /trading_bot/constants.py (WERSJA FINALNA v6.5)

import os
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Firestore ---
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"

# Architektura Wielo-Kolekcyjna
SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "open_trades"
ANALYZED_COLLECTION = "analyzed_trades"

# --- Bybit API ---
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"
BYBIT_DEFAULT_CATEGORY = "linear"

# --- Klucze API ---
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# --- Konfiguracja Analityki BigQuery ---
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"