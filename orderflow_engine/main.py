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

VP_SEED_RETRIES = int(os.environ.get("VP_SEED_RETRIES", "5"))
VP_SEED_M1_LIMIT = int(os.environ.get("VP_SEED_M1_LIMIT", "280"))
VP_SEED_CONCURRENCY = int(os.environ.get("VP_SEED_CONCURRENCY", "4"))
VP_SEED_BACKOFF_SEC = [2, 5, 15, 30, 60]


async def _fetch_symbol_vp_and_msi(symbol: str, processor: OrderFlowMetrics, backfiller: HistoryBackfiller, semaphore: asyncio.Semaphore) -> None:
    """
    Per-symbol: REST M1 → bulk VP seed (priorytet), potem D1 + MSI bootstrap.
    Retry z backoffem — bez globalnego gather+timeout anulującego symbole.
    """
    sym = str(symbol).upper()
    for attempt in range(VP_SEED_RETRIES):
        async with semaphore:
            try:
                r_m1 = await backfiller.fetch_history(symbol, "1", VP_SEED_M1_LIMIT)
                if not r_m1.ok or not r_m1.rows:
                    logger.warning(
                        "[VP-SEED] %s M1 pusty/nieudany ok=%s n=%s próba %s/%s",
                        sym, r_m1.ok, len(r_m1.rows) if r_m1.ok else 0, attempt + 1, VP_SEED_RETRIES,
                    )
                else:
                    if processor.seed_vp_buffer(symbol, r_m1.rows):
                        await asyncio.sleep(0.5)
                        r_d1 = await backfiller.fetch_history(symbol, "D", 365)
                        if r_d1.ok and r_d1.rows:
                            processor.bootstrap_msi_structure(symbol, r_m1.rows, r_d1.rows)
                        else:
                            processor.bootstrap_msi_structure(symbol, r_m1.rows, [])
                            logger.warning("[VP-SEED] %s D1 pominięty — MSI z samym M1", sym)
                        logger.info(
                            "[VP-SEED] %s OK M1=%d D1=%s",
                            sym, len(r_m1.rows), len(r_d1.rows) if r_d1.ok else 0,
                        )
                        return
                    logger.warning(
                        "[VP-SEED] %s bulk seed niewystarczający próba %s/%s",
                        sym, attempt + 1, VP_SEED_RETRIES,
                    )
            except Exception as e:
                logger.warning(
                    "[VP-SEED] %s próba %s/%s: %s: %s",
                    sym, attempt + 1, VP_SEED_RETRIES, type(e).__name__, e,
                    exc_info=True,
                )
        if attempt + 1 < VP_SEED_RETRIES:
            backoff = VP_SEED_BACKOFF_SEC[min(attempt, len(VP_SEED_BACKOFF_SEC) - 1)]
            await asyncio.sleep(backoff)
    processor.mark_vp_seed_failed(symbol)
    logger.error("[VP-SEED] %s FAILED po %s próbach — OB_NEW będzie z vp_seed_failed=true", sym, VP_SEED_RETRIES)


async def run_vp_seed_parallel(processor: OrderFlowMetrics) -> None:
    """VP seed + MSI bootstrap per symbol, równolegle z WS (nie czeka na subscribe)."""
    symbols = ALL_SYMBOLS_FOR_WS
    logger.info(
        "[VP-SEED] Start dla %s symboli (conc=%s, m1_limit=%s, retries=%s)",
        len(symbols), VP_SEED_CONCURRENCY, VP_SEED_M1_LIMIT, VP_SEED_RETRIES,
    )
    semaphore = asyncio.Semaphore(VP_SEED_CONCURRENCY)
    async with HistoryBackfiller() as backfiller:
        tasks = [
            asyncio.create_task(_fetch_symbol_vp_and_msi(sym, processor, backfiller, semaphore))
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    ready = sum(1 for s in symbols if processor.is_vp_seed_ready(s))
    failed = sum(1 for s in symbols if processor._vp_status(s) == "failed")
    for sym, res in zip(symbols, results):
        if isinstance(res, BaseException):
            logger.error("[VP-SEED] task %s: %s: %s", sym, type(res).__name__, res, exc_info=res)
    logger.info("[VP-SEED] Zakończono ready=%s failed=%s total=%s", ready, failed, len(symbols))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global METRICS_PROCESSOR, CONTEXT_BUILDER
    ws_manager = None
    try:
        logger.info("🚀 Starting OrderFlow Engine V7.5...")
        if not await asyncio.to_thread(initialize_firebase):
            raise RuntimeError("initialize_firebase() zwróciło False — brak klienta Firestore")
        if not await asyncio.to_thread(verify_firestore_connection):
            raise RuntimeError("verify_firestore_connection() nie powiodło się")
        db = get_db()
        set_context_firestore_client(db)
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=db)
        logger.info(
            "SIGNAL_MODE=%s | MSI engine=%s | footprint evaluate=%s",
            get_signal_mode(),
            msi_engine_enabled(),
            footprint_alerts_enabled(),
        )
        CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)
        ws_manager = MultiConnectionWSManager(symbols=ALL_SYMBOLS_FOR_WS, metrics_processor=METRICS_PROCESSOR)
        asyncio.create_task(ws_manager.start_all_connections())

        # VP seed równolegle z WS — bez opóźnienia subscribe, bez globalnego timeoutu.
        asyncio.create_task(run_vp_seed_parallel(METRICS_PROCESSOR))

        logger.info("✅ OrderFlow Engine startup complete (WS + VP seed równolegle)")
    except Exception as e:
        logger.critical(f"💀 STARTUP FAILED: {e}", exc_info=True)
    yield
    logger.info("🛑 OrderFlow Engine shutting down")
    if ws_manager is not None:
        ws_manager.is_running = False

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
