# orderflow_engine/main.py
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException

from .config_symbols import SYMBOLS_TO_WATCH_CLEAN, BENCHMARK_SYMBOL
from .metrics_processor import OrderFlowMetrics
from .websocket_handler import websocket_listener

# --- Konfiguracja ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Inicjalizacja Stanu ---
METRICS_PROCESSOR = OrderFlowMetrics(
    symbols=SYMBOLS_TO_WATCH_CLEAN,
    benchmark=BENCHMARK_SYMBOL
)

# --- FastAPI Setup ---
app = FastAPI(title="OrderFlow Engine")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(
        websocket_listener(
            METRICS_PROCESSOR,
            METRICS_PROCESSOR.process_trade,
            METRICS_PROCESSOR.process_ticker
        )
    )
    logger.info("OrderFlow Engine startup complete. WS listener started.")

@app.get("/health")
async def health_check():
    btc_state = METRICS_PROCESSOR.states.get(BENCHMARK_SYMBOL)

    is_btc_fresh = (
        btc_state
        and btc_state.last_update_ts
        and (datetime.now(timezone.utc) - btc_state.last_update_ts < timedelta(seconds=30))
    )

    status = "healthy" if is_btc_fresh else "unhealthy"

    return {
        "status": status,
        "ws_active": True,  # opcjonalnie mogę dodać realne sprawdzanie
        "btc_price_fresh": bool(is_btc_fresh)
    }

@app.get("/metrics")
async def get_metrics_endpoint(symbol: str):
    symbol_clean = symbol.upper().replace('.P', '')

    if symbol_clean not in METRICS_PROCESSOR.states:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol {symbol_clean} nie jest śledzony."
        )

    return METRICS_PROCESSOR.get_metrics_json(symbol_clean)