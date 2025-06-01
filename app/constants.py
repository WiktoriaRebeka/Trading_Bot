# trading_bot/app/constants.py
import os
from dotenv import load_dotenv

# Ścieżka do pliku .env jest relatywna do głównego katalogu projektu (TRADING_BOT)
# os.path.dirname(__file__) -> app/
# os.path.dirname(os.path.dirname(__file__)) -> TRADING_BOT/
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path) # To załaduje zmienne z .env do os.environ

# Firestore
FIRESTORE_COLLECTION_ALERTS = "alerts"
# Możesz tu dodać inne stałe związane z Firestore, jeśli chcesz, np.:
# FIRESTORE_BOT_CONFIG_COLLECTION = "bot_config"
# FIRESTORE_POSITIONS_COLLECTION = "trading_positions"

# Bybit API
BYBIT_API_URL_V5_TICKERS = "https://api.bybit.com/v5/market/tickers" # Dla cen
BYBIT_DEFAULT_CATEGORY = "linear" # lub "spot", "inverse" w zależności od potrzeb

# Pliki lokalne - te stałe nie są już potrzebne dla logiki App Engine
# LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt" # USUNIĘTE
# POSITIONS_LOG_FILE = "positions_log.jsonl" # USUNIĘTE

# Ustawienia bota
FETCH_INTERVAL_SECONDS = int(os.getenv("FETCH_INTERVAL_SECONDS", 60))
BOT_LOOP_INTERVAL_SECONDS = int(os.getenv("BOT_LOOP_INTERVAL_SECONDS", 15))
MAX_ALERT_AGE_SECONDS = int(os.getenv("MAX_ALERT_AGE_SECONDS", 120))

# Przykład odczytu zmiennej środowiskowej dla klucza API, jeśli kiedyś będziesz potrzebować
# BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
# BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# if not BYBIT_API_KEY or not BYBIT_API_SECRET:
#     print("[CONSTANTS_WARN] Klucze API Bybit nie są ustawione w zmiennych środowiskowych.")