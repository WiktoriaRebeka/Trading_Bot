# orderflow_engine/main.py
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager

from orderflow_engine.websocket_handler import MultiConnectionWSManager
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.config_symbols import SYMBOLS_TO_WATCH_CLEAN
from orderflow_engine.backfiller import HistoryBackfiller
from shared_lib.firebase_client import initialize_firebase, get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

METRICS_PROCESSOR = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR
    logger.info("🚀 Starting OrderFlow Engine V6.1 (Autonomous + Backfill)...")
    
    # 1. Inicjalizacja Firebase
    if initialize_firebase():
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
        logger.info("✅ Firebase i MetricsProcessor gotowe.")
    else:
        logger.error("❌ Błąd krytyczny: Brak połączenia z Firestore.")
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=None)

    # 2. BACKFILL - Pobieranie historii przed startem WS
    backfiller = HistoryBackfiller()
    for symbol in SYMBOLS_TO_WATCH_CLEAN:
        logger.info(f"📥 Backfilling {symbol}...")
        h_m1 = backfiller.fetch_history(symbol, interval='1', limit=1000)
        h_d1 = backfiller.fetch_history(symbol, interval='D', limit=365)
        if METRICS_PROCESSOR:
            METRICS_PROCESSOR.pre_load_history(symbol, h_m1, h_d1)

    # 3. Uruchomienie połączeń Bybit w tle
    ws_manager = MultiConnectionWSManager(
        symbols=SYMBOLS_TO_WATCH_CLEAN,
        metrics_processor=METRICS_PROCESSOR
    )
    
    task = asyncio.create_task(ws_manager.start_all_connections())
    
    yield  # Tutaj aplikacja działa
    
    logger.info("🛑 Zamykanie silnika...")
    await ws_manager.shutdown()
    task.cancel()

# Tworzymy aplikację JEDEN RAZ z poprawną funkcją lifespan
app = FastAPI(title="OrderFlow Engine V6.1", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/metrics")
async def get_metrics(symbol: str):
    if METRICS_PROCESSOR is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    
    symbol_clean = symbol.upper().replace('.P', '')
    try:
        return METRICS_PROCESSOR.get_full_context(symbol_clean)
    except Exception as e:
        logger.error(f"Error for {symbol_clean}: {e}")
        raise HTTPException(status_code=404, detail="Symbol not found")