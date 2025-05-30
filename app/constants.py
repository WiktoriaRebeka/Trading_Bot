# trading_bot/app/constants.py
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env')) # Wskazanie ścieżki do .env

# Supabase REST API (nadal może być potrzebne dla webhooka)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_TABLE_NAME = os.getenv("SUPABASE_TABLE_NAME", "alerts")
SUPABASE_API_KEY = os.getenv("SUPABASE_ANON_KEY") # To jest anon_key

# Supabase PostgreSQL Direct Connection
SUPABASE_DB_USER = os.getenv("SUPABASE_DB_USER")
SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD")
SUPABASE_DB_HOST = os.getenv("SUPABASE_DB_HOST")
SUPABASE_DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432") # Domyślny port
SUPABASE_DB_NAME = os.getenv("SUPABASE_DB_NAME")
# Bybit API
BYBIT_API_URL_V5_TICKERS = "https://api.bybit.com/v5/market/tickers" # Dla cen

# Pliki lokalne
LAST_PROCESSED_TIMESTAMP_FILE = "last_processed_timestamp.txt"
POSITIONS_LOG_FILE = "positions_log.jsonl"

# Ustawienia bota
FETCH_INTERVAL_SECONDS = 60  # Jak często pobierać alerty z Supabase
BOT_LOOP_INTERVAL_SECONDS = 15 # Jak często pętla logiki bota ma się wykonywać
MAX_ALERT_AGE_SECONDS = 120 # Maksymalny wiek alertu, aby uznać go za "świeży"

# Domyślna kategoria dla Bybit (dostosuj jeśli używasz spot/inverse)
BYBIT_DEFAULT_CATEGORY = "linear" # Dla kontraktów USDT perpetuals