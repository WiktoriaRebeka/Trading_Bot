# TRADING_BOT/constants.py
import os
from dotenv import load_dotenv

# To jest potrzebne tylko do testów lokalnych, na GAE nie będzie używane
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

# Firestore
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"
LAST_FETCH_STATE_DOC_ID = "last_fetch_state"
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp"
POSITIONS_COLLECTION_FIRESTORE = "trading_positions"

# Bybit API
BYBIT_API_URL_V5_TICKERS = "https://api.bybit.com/v5/market/tickers"
BYBIT_DEFAULT_CATEGORY = "linear"

# Ustawienia bota
MAX_ALERT_AGE_SECONDS = int(os.getenv("MAX_ALERT_AGE_SECONDS", 70))

# === AKTYWACJA WCZYTYWANIA KLUCZY API ===
# Te zmienne zostaną wypełnione wartościami z pliku app.yaml na serwerze
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# Ostrzeżenie, jeśli klucze nie zostaną znalezione
if not BYBIT_API_KEY or not BYBIT_API_SECRET:
   print("[CONSTANTS_WARN] Klucze API Bybit nie są ustawione w zmiennych środowiskowych.")