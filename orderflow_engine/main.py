# orderflow_engine/main.py
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from orderflow_engine.websocket_handler import MultiConnectionWSManager
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.config_symbols import SYMBOLS_TO_WATCH_CLEAN
from orderflow_engine.backfiller import HistoryBackfiller
from orderflow_engine.integration import SignalContextBuilder
from shared_lib.firebase_client import initialize_firebase, get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

METRICS_PROCESSOR: OrderFlowMetrics | None = None
CONTEXT_BUILDER: SignalContextBuilder | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER
    logger.info("🚀 Starting OrderFlow Engine V6.1 (Autonomous + Backfill)...")

    # 1. Firebase + Metrics
    if initialize_firebase():
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
        logger.info("✅ Firebase i MetricsProcessor gotowe.")
    else:
        logger.error("❌ Błąd krytyczny: Brak połączenia z Firestore.")
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=None)

    CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)

    # 2. BACKFILL
    backfiller = HistoryBackfiller()
    for symbol in SYMBOLS_TO_WATCH_CLEAN:
        logger.info(f"📥 Backfilling {symbol}...")
        h_m1 = backfiller.fetch_history(symbol, interval='1', limit=1000)
        h_d1 = backfiller.fetch_history(symbol, interval='D', limit=365)
        METRICS_PROCESSOR.pre_load_history(symbol, h_m1, h_d1)

    # 3. WebSocket manager
    ws_manager = MultiConnectionWSManager(
        symbols=SYMBOLS_TO_WATCH_CLEAN,
        metrics_processor=METRICS_PROCESSOR,
    )

    task = asyncio.create_task(ws_manager.start_all_connections())

    try:
        yield
    finally:
        logger.info("🛑 Zamykanie silnika...")
        await ws_manager.shutdown()
        task.cancel()


app = FastAPI(title="OrderFlow Engine V6.1", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/metrics")
async def get_metrics(symbol: str):
    if METRICS_PROCESSOR is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    symbol_clean = symbol.upper().replace(".P", "")
    try:
        ctx = METRICS_PROCESSOR.get_full_context(symbol_clean)
        return ctx
    except Exception as e:
        logger.error(f"Error for {symbol_clean}: {e}")
        raise HTTPException(status_code=404, detail="Symbol not found")


@app.get("/context/{symbol}")
async def get_signal_context(symbol: str):
    """
    Zwraca aktualny kontekst mikrostruktury dla danego symbolu.
    Używane przez bot_service / ORDERFLOW_CLIENT.
    """
    if CONTEXT_BUILDER is None:
        raise HTTPException(status_code=503, detail="Engine not ready")

    symbol_clean = symbol.upper().replace(".P", "")
    try:
        ctx: Dict[str, Any] = CONTEXT_BUILDER.build_context(symbol_clean)
        return JSONResponse(content={"status": "ok", "data": ctx}, status_code=200)
    except Exception as e:
        logger.exception(f"[CONTEXT] Error building context for {symbol_clean}: {e}")
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )