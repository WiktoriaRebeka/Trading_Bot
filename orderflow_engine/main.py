# orderflow_engine/main.py
# WERSJA: 5.2 - FastAPI + Background Engine

import asyncio
import logging
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager

# IMPORTY ABSOLUTNE (Klucz do sukcesu w Dockerze)
from orderflow_engine.websocket_handler import MultiConnectionWSManager
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.config_symbols import SYMBOLS_TO_WATCH_CLEAN
from shared_lib.firebase_client import initialize_firebase, get_db

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Globalny procesor metryk
METRICS_PROCESSOR = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Zarządza cyklem życia silnika (Start/Stop)"""
    global METRICS_PROCESSOR
    logger.info("🚀 Startowanie OrderFlow Engine V5.2...")
    
    # 1. Firebase
    if initialize_firebase():
        firestore_client = get_db()
        logger.info("✅ Firestore połączony")
    else:
        logger.error("❌ Błąd połączenia z Firestore")
        firestore_client = None

    # 2. Inicjalizacja procesora
    METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=firestore_client)
    
    # 3. Uruchomienie WebSocketów w tle
    ws_manager = MultiConnectionWSManager(
        symbols=SYMBOLS_TO_WATCH_CLEAN,
        metrics_processor=METRICS_PROCESSOR
    )
    
    # Tworzymy task w tle, żeby nie blokować serwera API
    bg_task = asyncio.create_task(ws_manager.start_all_connections())
    
    yield  # Tutaj aplikacja "żyje"
    
    # 4. Shutdown
    logger.info("🛑 Zamykanie silnika...")
    await ws_manager.shutdown()
    bg_task.cancel()

# --- Aplikacja FastAPI ---
app = FastAPI(title="OrderFlow Engine V5.2", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "active"}

@app.get("/metrics")
async def get_metrics(symbol: str):
    """Endpoint dla Bot Service do pobierania metryk"""
    if METRICS_PROCESSOR is None:
        raise HTTPException(status_code=503, detail="Silnik jeszcze się inicjalizuje")
    
    symbol_clean = symbol.upper().replace('.P', '')
    # Wywołujemy funkcję get_full_context, którą dopisaliśmy do metrics_processor.py
    try:
        return METRICS_PROCESSOR.get_full_context(symbol_clean)
    except Exception as e:
        logger.error(f"Błąd pobierania metryk dla {symbol_clean}: {e}")
        raise HTTPException(status_code=404, detail=f"Brak danych dla symbolu {symbol_clean}")