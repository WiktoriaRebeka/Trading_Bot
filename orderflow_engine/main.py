# orderflow_engine/main.py
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from orderflow_engine.websocket_handler import MultiConnectionWSManager
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.config_symbols import SYMBOLS_TO_WATCH_CLEAN
from orderflow_engine.backfiller import HistoryBackfiller
from orderflow_engine.integration import SignalContextBuilder, get_global_context
from shared_lib.firebase_client import initialize_firebase, get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

METRICS_PROCESSOR: OrderFlowMetrics | None = None
CONTEXT_BUILDER: SignalContextBuilder | None = None

async def run_backfill_in_background(processor: OrderFlowMetrics):
    """Pobiera historię w tle, aby nie blokować startu bota."""
    backfiller = HistoryBackfiller()
    logger.info(f"📥 Rozpoczynam Backfill w tle dla {len(SYMBOLS_TO_WATCH_CLEAN)} symboli...")
    
    for symbol in SYMBOLS_TO_WATCH_CLEAN:
        try:
            # Używamy run_in_executor, bo backfiller używa synchronicznego 'requests'
            loop = asyncio.get_event_loop()
            h_m1 = await loop.run_in_executor(None, backfiller.fetch_history, symbol, '1', 1000)
            h_d1 = await loop.run_in_executor(None, backfiller.fetch_history, symbol, 'D', 365)
            
            processor.pre_load_history(symbol, h_m1, h_d1)
            logger.info(f"✅ {symbol} Backfill OK.")
            # Mała pauza, żeby nie dostać bana za Rate Limit przy starcie
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"❌ Błąd backfillu dla {symbol}: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER
    logger.info("🚀 Starting OrderFlow Engine V7.2 (Async Startup)...")

    # 1. Inicjalizacja Firebase
    if not initialize_firebase():
        logger.error("❌ Błąd krytyczny: Brak połączenia z Firestore.")
        # W trybie mission-critical tutaj powinien być raise, ale pozwalamy wstać dla debugu
    
    METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
    CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)

    # 2. Uruchomienie WebSocketów (NATYCHMIAST)
    ws_manager = MultiConnectionWSManager(
        symbols=SYMBOLS_TO_WATCH_CLEAN,
        metrics_processor=METRICS_PROCESSOR,
    )
    ws_task = asyncio.create_task(ws_manager.start_all_connections())

    # 3. Uruchomienie Backfillu (W TLE)
    backfill_task = asyncio.create_task(run_backfill_in_background(METRICS_PROCESSOR))

    yield

    logger.info("🛑 Zamykanie silnika...")
    ws_manager.is_running = False
    ws_task.cancel()
    backfill_task.cancel()

app = FastAPI(title="OrderFlow Engine V7.2", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/context/{symbol}")
async def get_signal_context(symbol: str):
    symbol_clean = symbol.upper().replace(".P", "")
    # Pobieramy z Ingestion Layer (Cache) - czas odpowiedzi < 1ms
    ctx = get_global_context(symbol_clean)
    if ctx:
        return JSONResponse(content={"status": "ok", "data": ctx})
    
    # Jeśli nie ma w cache, spróbuj zbudować (fallback)
    if CONTEXT_BUILDER:
        ctx = CONTEXT_BUILDER.build_context(symbol_clean)
        return JSONResponse(content={"status": "ok", "data": ctx})
    
    raise HTTPException(status_code=503, detail="Engine not ready")