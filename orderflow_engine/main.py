
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
# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Globalny procesor metryk
METRICS_PROCESSOR = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR
    logger.info("🚀 Starting OrderFlow Engine V6.0 (Autonomous)...")
    
    # 1. Init Firebase
    initialize_firebase()
    METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
    
    # 2. Backfill (Z głową - najpierw historia, potem Live)
    backfiller = HistoryBackfiller()
    for symbol in SYMBOLS_TO_WATCH_CLEAN:
        logger.info(f"📥 Backfilling {symbol}...")
        h_m1 = backfiller.fetch_history(symbol, interval='1', limit=1000)
        h_d1 = backfiller.fetch_history(symbol, interval='D', limit=365)
        METRICS_PROCESSOR.pre_load_history(symbol, h_m1, h_d1)

    # 3. Start WebSocket Manager
    ws_manager = MultiConnectionWSManager(SYMBOLS_TO_WATCH_CLEAN, METRICS_PROCESSOR)
    task = asyncio.create_task(ws_manager.start_all_connections())
    
    yield
    await ws_manager.shutdown()
    task.cancel()

app = FastAPI(title="OrderFlow Engine V6.0", lifespan=lifespan)

@app.get("/metrics")
async def get_metrics(symbol: str):
    if not METRICS_PROCESSOR: raise HTTPException(status_code=503)
    return METRICS_PROCESSOR.get_full_context(symbol.upper())

@app.get("/health")
async def health(): return {"status": "online"}




