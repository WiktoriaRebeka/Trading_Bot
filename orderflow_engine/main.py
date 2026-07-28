# orderflow_engine/main.py
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from orderflow_engine.websocket_handler import MultiConnectionWSManager
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.config_symbols import ALL_SYMBOLS_FOR_WS
from orderflow_engine.backfiller import HistoryBackfiller
from orderflow_engine.volume_profile_fetcher import VolumeProfileFetcher
from orderflow_engine.msi_state_persister import MsiStatePersister
from orderflow_engine.integration import (
    SignalContextBuilder,
    get_global_context,
    load_remote_context_firestore,
    prime_context_memory,
    set_context_firestore_client,
)
from shared_lib.firebase_client import initialize_firebase, get_db, verify_firestore_connection
from shared_lib.signal_mode import get_signal_mode, msi_engine_enabled, footprint_alerts_enabled

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

METRICS_PROCESSOR: OrderFlowMetrics | None = None
CONTEXT_BUILDER: SignalContextBuilder | None = None
VP_FETCHER: VolumeProfileFetcher | None = None

MSI_BOOTSTRAP_RETRIES = int(os.environ.get("MSI_BOOTSTRAP_RETRIES", os.environ.get("VP_SEED_RETRIES", "5")))
MSI_BOOTSTRAP_M1_LIMIT = int(os.environ.get("MSI_BOOTSTRAP_M1_LIMIT", os.environ.get("VP_SEED_M1_LIMIT", "280")))
MSI_BOOTSTRAP_CONCURRENCY = int(os.environ.get("MSI_BOOTSTRAP_CONCURRENCY", os.environ.get("VP_SEED_CONCURRENCY", "4")))
MSI_BOOTSTRAP_BACKOFF_SEC = [2, 5, 15, 30, 60]


async def _fetch_symbol_msi_bootstrap(
    symbol: str,
    processor: OrderFlowMetrics,
    backfiller: HistoryBackfiller,
    semaphore: asyncio.Semaphore,
) -> None:
    """Per-symbol: REST M1 + D1 → MSI bootstrap (bez bufora VP w pamięci)."""
    sym = str(symbol).upper()
    for attempt in range(MSI_BOOTSTRAP_RETRIES):
        async with semaphore:
            try:
                r_m1 = await backfiller.fetch_history(symbol, "1", MSI_BOOTSTRAP_M1_LIMIT)
                if not r_m1.ok or not r_m1.rows:
                    logger.warning(
                        "[MSI-BOOTSTRAP] %s M1 pusty/nieudany ok=%s n=%s próba %s/%s",
                        sym, r_m1.ok, len(r_m1.rows) if r_m1.ok else 0, attempt + 1, MSI_BOOTSTRAP_RETRIES,
                    )
                else:
                    await asyncio.sleep(0.5)
                    r_d1 = await backfiller.fetch_history(symbol, "D", 365)
                    if r_d1.ok and r_d1.rows:
                        processor.bootstrap_msi_structure(symbol, r_m1.rows, r_d1.rows)
                    else:
                        processor.bootstrap_msi_structure(symbol, r_m1.rows, [])
                        logger.warning("[MSI-BOOTSTRAP] %s D1 pominięty — MSI z samym M1", sym)
                    logger.info(
                        "[MSI-BOOTSTRAP] %s OK M1=%d D1=%s",
                        sym, len(r_m1.rows), len(r_d1.rows) if r_d1.ok else 0,
                    )
                    return
            except Exception as e:
                logger.warning(
                    "[MSI-BOOTSTRAP] %s próba %s/%s: %s: %s",
                    sym, attempt + 1, MSI_BOOTSTRAP_RETRIES, type(e).__name__, e,
                    exc_info=True,
                )
        if attempt + 1 < MSI_BOOTSTRAP_RETRIES:
            backoff = MSI_BOOTSTRAP_BACKOFF_SEC[min(attempt, len(MSI_BOOTSTRAP_BACKOFF_SEC) - 1)]
            await asyncio.sleep(backoff)
    logger.error("[MSI-BOOTSTRAP] %s FAILED po %s próbach", sym, MSI_BOOTSTRAP_RETRIES)


async def run_msi_bootstrap_parallel(processor: OrderFlowMetrics) -> None:
    """MSI bootstrap per symbol, równolegle z WS. Na koniec odblokowuje VP fetch."""
    symbols = ALL_SYMBOLS_FOR_WS
    logger.info(
        "[MSI-BOOTSTRAP] Start dla %s symboli (conc=%s, m1_limit=%s, retries=%s)",
        len(symbols), MSI_BOOTSTRAP_CONCURRENCY, MSI_BOOTSTRAP_M1_LIMIT, MSI_BOOTSTRAP_RETRIES,
    )
    semaphore = asyncio.Semaphore(MSI_BOOTSTRAP_CONCURRENCY)
    try:
        async with HistoryBackfiller() as backfiller:
            tasks = [
                asyncio.create_task(_fetch_symbol_msi_bootstrap(sym, processor, backfiller, semaphore))
                for sym in symbols
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, res in zip(symbols, results):
            if isinstance(res, BaseException):
                logger.error("[MSI-BOOTSTRAP] task %s: %s: %s", sym, type(res).__name__, res, exc_info=res)
        logger.info("[MSI-BOOTSTRAP] Zakończono total=%s", len(symbols))
    finally:
        if VP_FETCHER is not None:
            VP_FETCHER.mark_bootstrap_complete()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER, VP_FETCHER
    ws_manager = None
    try:
        logger.info("🚀 Starting OrderFlow Engine V7.5...")
        if not await asyncio.to_thread(initialize_firebase):
            raise RuntimeError("initialize_firebase() zwróciło False — brak klienta Firestore")
        if not await asyncio.to_thread(verify_firestore_connection):
            raise RuntimeError("verify_firestore_connection() nie powiodło się")
        db = get_db()
        set_context_firestore_client(db)
        MsiStatePersister.start()
        VP_FETCHER = VolumeProfileFetcher()
        await VP_FETCHER.start()
        VP_FETCHER.begin_bootstrap_hold()
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=db, vp_fetcher=VP_FETCHER)
        logger.info(
            "SIGNAL_MODE=%s | MSI engine=%s | footprint evaluate=%s",
            get_signal_mode(),
            msi_engine_enabled(),
            footprint_alerts_enabled(),
        )
        CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)
        ws_manager = MultiConnectionWSManager(symbols=ALL_SYMBOLS_FOR_WS, metrics_processor=METRICS_PROCESSOR)
        asyncio.create_task(ws_manager.start_all_connections())

        asyncio.create_task(run_msi_bootstrap_parallel(METRICS_PROCESSOR))

        logger.info("✅ OrderFlow Engine startup complete (WS + MSI bootstrap równolegle; VP hold do końca bootstrap)")
    except Exception as e:
        if VP_FETCHER is not None:
            VP_FETCHER.mark_bootstrap_complete()
        logger.critical(f"💀 STARTUP FAILED: {e}", exc_info=True)
    yield
    logger.info("🛑 OrderFlow Engine shutting down")
    MsiStatePersister.stop()
    if ws_manager is not None:
        ws_manager.is_running = False
    if VP_FETCHER is not None:
        await VP_FETCHER.close()

app = FastAPI(title="OrderFlow Engine V7.5", lifespan=lifespan)

@app.get("/health")
async def health(): return {"status": "healthy"}

@app.get("/debug-ip")
async def debug_ip():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
    return {"egress_ip": data.get("ip"), "expected": "34.158.226.157"}

@app.get("/msi-state/{symbol}")
async def get_msi_state(symbol: str):
    symbol_clean = symbol.upper().replace(".P", "")
    if METRICS_PROCESSOR is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    state = METRICS_PROCESSOR.get_msi_state(symbol_clean)
    if state is None:
        return JSONResponse(
            content={
                "status": "ok",
                "data": None,
                "message": "MSI engine disabled or symbol not yet fed",
            }
        )
    return JSONResponse(content={"status": "ok", "data": state})

@app.get("/context/{symbol}")
async def get_signal_context(symbol: str):
    symbol_clean = symbol.upper().replace(".P", "")
    ctx = get_global_context(symbol_clean)
    if ctx is None:
        remote = await asyncio.to_thread(load_remote_context_firestore, symbol_clean)
        if remote:
            prime_context_memory(symbol_clean, remote)
            ctx = remote
    if ctx: return JSONResponse(content={"status": "ok", "data": ctx})
    if CONTEXT_BUILDER:
        ctx = CONTEXT_BUILDER.build_context(symbol_clean)
        return JSONResponse(content={"status": "ok", "data": ctx})
    raise HTTPException(status_code=503, detail="Engine not ready")
