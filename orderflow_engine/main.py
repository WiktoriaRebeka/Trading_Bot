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

async def fetch_single_backfill(symbol, processor, backfiller, semaphore):
    """Pobiera historię z mechanizmem ponawiania (Retry)."""
    async with semaphore:
        for attempt in range(3): # 3 próby dla każdego symbolu
            try:
                h_m1 = await backfiller.fetch_history(symbol, '1', 1000)
                await asyncio.sleep(0.2) # Oddech dla API
                h_d1 = await backfiller.fetch_history(symbol, 'D', 365)
                
                processor.pre_load_history(symbol, h_m1, h_d1)
                logger.info(f"✅ {symbol} Backfill OK.")
                return # Sukces
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1)) # Progresywny delay
                else:
                    logger.error(f"❌ Błąd backfillu dla {symbol} po 3 próbach: {e}")

async def run_backfill_in_background(processor: OrderFlowMetrics):
    backfiller = HistoryBackfiller()
    semaphore = asyncio.Semaphore(3) # Zmniejszamy do 3, żeby nie drażnić Bybit Rate Limit
    
    logger.info(f"📥 Rozpoczynam Backfill dla {len(SYMBOLS_TO_WATCH_CLEAN)} symboli...")
    tasks = [fetch_single_backfill(symbol, processor, backfiller, semaphore) for symbol in SYMBOLS_TO_WATCH_CLEAN]
    await asyncio.gather(*tasks)
    logger.info("🚀 WSZYSTKIE SYMBOLE ZAŁADOWANE. Bot jest w pełni gotowy.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER
    logger.info("🚀 Starting OrderFlow Engine V7.4 (Resilient Startup)...")

    initialize_firebase()
    METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
    CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)

    ws_manager = MultiConnectionWSManager(symbols=SYMBOLS_TO_WATCH_CLEAN, metrics_processor=METRICS_PROCESSOR)
    ws_task = asyncio.create_task(ws_manager.start_all_connections())
    backfill_task = asyncio.create_task(run_backfill_in_background(METRICS_PROCESSOR))

    yield
    ws_manager.is_running = False

app = FastAPI(title="OrderFlow Engine V7.4", lifespan=lifespan)

@app.get("/health")
async def health(): return {"status": "healthy"}

@app.get("/context/{symbol}")
async def get_signal_context(symbol: str):
    symbol_clean = symbol.upper().replace(".P", "")
    ctx = get_global_context(symbol_clean)
    if ctx: return JSONResponse(content={"status": "ok", "data": ctx})
    if CONTEXT_BUILDER:
        ctx = CONTEXT_BUILDER.build_context(symbol_clean)
        return JSONResponse(content={"status": "ok", "data": ctx})
    raise HTTPException(status_code=503, detail="Engine not ready")