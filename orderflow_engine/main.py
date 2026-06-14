# orderflow_engine/main.py
import asyncio
import logging
import os
import time
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def _monitor_loop_lag():
    while True:
        start = time.perf_counter()
        await asyncio.sleep(1)
        lag = time.perf_counter() - start - 1
        if lag > 0.25:
            logger.warning(f"EVENT_LOOP_LAG={lag:.3f}s")


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
    symbols = ALL_SYMBOLS_FOR_WS
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
        if not await asyncio.to_thread(initialize_firebase):
            raise RuntimeError("initialize_firebase() zwróciło False — brak klienta Firestore")
        if not await asyncio.to_thread(verify_firestore_connection):
            raise RuntimeError("verify_firestore_connection() nie powiodło się")
        db = get_db()
        set_context_firestore_client(db)
        METRICS_PROCESSOR = OrderFlowMetrics(firestore_client=db)
        CONTEXT_BUILDER = SignalContextBuilder(METRICS_PROCESSOR)
        ws_manager = MultiConnectionWSManager(symbols=ALL_SYMBOLS_FOR_WS, metrics_processor=METRICS_PROCESSOR)
        asyncio.create_task(ws_manager.start_all_connections())

        async def _backfill_after_ws_subscriptions_ready():
            """
            Backfill dopiero po pierwszym sukcesie subscribe na każdym połączeniu (lub timeout),
            żeby REST Bybit nie konkurował z zestawianiem wszystkich WS przy starcie.
            """
            timeout = float(os.environ.get("WS_SUBSCRIBE_WAIT_TIMEOUT_SEC", "120"))
            ok = await ws_manager.wait_until_subscriptions_confirmed(timeout)
            if ok:
                logger.info(
                    "✅ Potwierdzono subskrypcję na wszystkich %s połączeniach WS — start backfillu w tle",
                    ws_manager.connection_count(),
                )
            await run_backfill_in_background(METRICS_PROCESSOR)

        asyncio.create_task(_backfill_after_ws_subscriptions_ready())
        logger.info("✅ OrderFlow Engine startup complete (WS startuje; backfill po potwierdzeniu subscribe)")
    except Exception as e:
        logger.critical(f"💀 STARTUP FAILED: {e}", exc_info=True)
    logger.warning("LAG_MONITOR_STARTED")
    asyncio.create_task(_monitor_loop_lag())
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
