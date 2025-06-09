# TRADING_BOT/app/constants.py
import os
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

# Firestore
FIRESTORE_COLLECTION_ALERTS = "alerts"
BOT_CONFIG_COLLECTION = "bot_config"  # <--- DODANE
LAST_FETCH_STATE_DOC_ID = "last_fetch_state" # <--- DODANE
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp" # <--- DODANE
POSITIONS_COLLECTION_FIRESTORE = "trading_positions" # <--- DODANE (używane w positions_logger.py)

# Bybit API
BYBIT_API_URL_V5_TICKERS = "https://api.bybit.com/v5/market/tickers"
BYBIT_DEFAULT_CATEGORY = "linear"

# Ustawienia bota
FETCH_INTERVAL_SECONDS = int(os.getenv("FETCH_INTERVAL_SECONDS", 60))
BOT_LOOP_INTERVAL_SECONDS = int(os.getenv("BOT_LOOP_INTERVAL_SECONDS", 15))
MAX_ALERT_AGE_SECONDS = int(os.getenv("MAX_ALERT_AGE_SECONDS", 120))

# Opcjonalne klucze API (zakomentowane, jeśli nie używasz)
# BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
# BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
# if not BYBIT_API_KEY or not BYBIT_API_SECRET:
#    print("[CONSTANTS_WARN] Klucze API Bybit nie są ustawione w zmiennych środowiskowych.")