# orderflow_engine/main.py
import asyncio
import logging
import os
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
    async with semaphore:
        for attempt in range(3):
            try:
                r_m1 = await backfiller.fetch_history(symbol, '1', 1000)
                await asyncio.sleep(0.8)  # Oddech dla Bybit REST (kline) przy równoległym WS
                r_d1 = await backfiller.fetch_history(symbol, 'D', 365)

                if not r_m1.ok or not r_m1.rows:
                    logger.warning(
                        f"⚠️ Backfill {symbol}: M1 nieudany lub pusty (ok={r_m1.ok}, n={len(r_m1.rows)}), "
                        f"próba {attempt + 1}/3"
                    )
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if not r_d1.ok or not r_d1.rows:
                    logger.warning(
                        f"⚠️ Backfill {symbol}: D1 nieudany lub pusty (ok={r_d1.ok}, n={len(r_d1.rows)}), "
                        f"próba {attempt + 1}/3"
                    )
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                processor.pre_load_history(symbol, r_m1.rows, r_d1.rows)
                logger.info(f"✅ {symbol} Backfill OK (M1={len(r_m1.rows)} D1={len(r_d1.rows)}).")
                return
            except Exception as e:
                logger.warning(
                    f"⚠️ Backfill {symbol} próba {attempt + 1}/3: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(2 * (attempt + 1))
        logger.error(f"❌ Błąd backfillu {symbol} po 3 próbach (brak poprawnych danych M1/D1).")


def _backfill_gather_timeout_sec(num_symbols: int) -> int:
    raw = os.environ.get("BACKFILL_GATHER_TIMEOUT")
    if raw is not None and raw.strip().isdigit():
        return max(60, int(raw))
    return max(300, min(900, num_symbols * 10))


async def run_backfill_in_background(processor: OrderFlowMetrics):
    semaphore = asyncio.Semaphore(2)  # Bezpieczne tempo dla Bybit
    symbols = SYMBOLS_TO_WATCH_CLEAN
    gather_timeout = _backfill_gather_timeout_sec(len(symbols))
    logger.info(f"📥 Start Backfill dla {len(symbols)} symboli (timeout gather={gather_timeout}s)...")

    async with HistoryBackfiller() as backfiller:
        task_objs = [
            asyncio.create_task(fetch_single_backfill(symbol, processor, backfiller, semaphore))
            for symbol in symbols
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*task_objs, return_exceptions=True),
                timeout=gather_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"⚠️ Backfill timeout po {gather_timeout}s — anulowanie pozostałych tasków, "
                "system startuje bez pełnej historii"
            )
            for t in task_objs:
                if not t.done():
                    t.cancel()
            results = await asyncio.gather(*task_objs, return_exceptions=True)
            for sym, res in zip(symbols, results):
                if isinstance(res, asyncio.CancelledError):
                    logger.debug(f"Backfill {sym}: anulowano (timeout)")
                elif isinstance(res, BaseException):
                    logger.error(
                        f"❌ Backfill task {sym} po anulowaniu: {type(res).__name__}: {res}",
                        exc_info=(type(res), res, res.__traceback__),
                    )
            logger.info("🚀 SYSTEM GOTOWY - Start bez backfillu.")
            return

        for sym, res in zip(symbols, results):
            if isinstance(res, asyncio.CancelledError):
                logger.debug(f"Backfill {sym}: anulowano")
            elif isinstance(res, BaseException):
                logger.error(
                    f"❌ Backfill task wyjątek {sym}: {type(res).__name__}: {res}",
                    exc_info=(type(res), res, res.__traceback__),
                )

        logger.info("🚀 SYSTEM GOTOWY - Wszystkie dane załadowane.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER
    ws_manager = None
    try:
        logger.info("🚀 Starting OrderFlow Engine V7.5...")
        initialize_firebase()
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=get_db())
        CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)
        ws_manager = MultiConnectionWSManager(symbols=SYMBOLS_TO_WATCH_CLEAN, metrics_processor=METRICS_PROCESSOR)
        asyncio.create_task(ws_manager.start_all_connections())
        asyncio.create_task(run_backfill_in_background(METRICS_PROCESSOR))
        logger.info("✅ OrderFlow Engine startup complete")
    except Exception as e:
        logger.critical(f"💀 STARTUP FAILED: {e}", exc_info=True)
    yield
    logger.info("🛑 OrderFlow Engine shutting down")
    if ws_manager is not None:
        ws_manager.is_running = False

app = FastAPI(title="OrderFlow Engine V7.5", lifespan=lifespan)

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
