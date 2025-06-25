# /trading_bot/constants.py (Wersja Finalna)

import os
from dotenv import load_dotenv

# Wczytywanie zmiennych środowiskowych z pliku .env dla testów lokalnych
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Firestore ---
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"

# NOWA, GŁÓWNA KOLEKCJA DO ZARZĄDZANIA STANEM TRANSAKCJI
SETUP_COLLECTION = "active_setups"
TRADE_COLLECTION = "active_trades"

# --- Bybit API ---
BYBIT_API_URL_V5_TICKERS = "https://api.bybit.com/v5/market/tickers"
BYBIT_API_URL_V5_KLINE = "https://api.bybit.com/v5/market/kline"  # <--- NOWA STAŁA
BYBIT_DEFAULT_CATEGORY = "linear"

# --- Ustawienia bota ---
MAX_ALERT_AGE_SECONDS = int(os.getenv("MAX_ALERT_AGE_SECONDS", 70))

# --- Klucze API (wypełniane ze zmiennych środowiskowych) ---
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
   print("[CONSTANTS_WARN] Klucze API Bybit nie są ustawione w zmiennych środowiskowych.")

# --- Konfiguracja Analityki BigQuery ---
BIGQUERY_PROJECT_ID = os.getenv("GCP_PROJECT", "trading-bot-463318")
BIGQUERY_DATASET_ID = "trading_analytics"
BIGQUERY_TABLE_ID = "trades_history"
