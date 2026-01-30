# orderflow_engine/main.py (Szkic)
import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Deque
from decimal import Decimal
from fastapi import FastAPI, HTTPException
from aiohttp import ClientSession, ClientError
from shared_lib.constants import BYBIT_API_URL_V5_KLINE # Użyjemy tego samego URL dla handlu/tickerów
from shared_lib.config import config # Zakładamy, że config jest dostępny
from shared_lib.firebase_client import get_db # Do pobierania symboli

# --- Konfiguracja ---
logger = logging.getLogger(__name__)
config.load() # Wczytanie kluczy API (jeśli potrzebne do WS)

# --- Stałe ---
SYMBOLS_TO_WATCH = [
    "DOGEUSDT", "TRXUSDT", "ADAUSDT", "XLMUSDT", "HBARUSDT", "ONDOUSDT", "ARBUSDT", 
    "ALGOUSDT", "POLUSDT", "KASUSDT", "XRPUSDT", "UNIUSDT", "TIAUSDT", "TAOUSDT", 
    "SUIUSDT", "SOLUSDT", "NEARUSDT", "LTCUSDT", "LINKUSDT", "ICPUSDT", "DOTUSDT", 
    "APTUSDT", "ATOMUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT", "AVAXUSDT", "BCHUSDT", 
    "AAVEUSDT", "WLDUSDT", "ENAUSDT", "SEIUSDT", "IMXUSDT", "RENDERUSDT", "ETCDUSDT", 
    "FLRUSDT", "TONUSDT", "OPUSDT", "INJUSDT", "HYPEUSDT", "PYTHUSDT", "STXUSDT", 
    "ORDIUSDT", "AXSUSDT", "SANDUSDT", "XMRUSDT", "MNTUSDT", "CROUSDT", "ZECUSDT"
]
BENCHMARK_SYMBOL = "BTCUSDT"
M2_DELTA_WINDOW_SECONDS = 120
EMA_RS_PERIOD = 5

# --- Stan Wewnętrzny ---
class SymbolMetrics:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.trades_buffer: Deque[tuple[int, int, float]] = asyncio.Queue() # (ts_ms, side_sign, qty)
        self.m2_delta: float = 0.0
        self.last_price: float = 0.0
        self.last_rs: float = 0.0
        self.m5_rs_ratio: float = 0.0
        self.ema_history: Deque[float] = asyncio.Queue() # Dla EMA_5

    def update_ema_rs(self, new_rs: float):
        if not self.ema_history:
            self.ema_history.append(new_rs)
            self.m5_rs_ratio = new_rs
            return

        if len(self.ema_history) < EMA_RS_PERIOD:
            self.ema_history.append(new_rs)
            # Proste uśrednianie do momentu osiągnięcia N punktów
            self.m5_rs_ratio = sum(self.ema_history) / len(self.ema_history)
        else:
            # EMA: alpha = 2 / (N + 1)
            alpha = 2 / (EMA_RS_PERIOD + 1)
            ema_old = self.m5_rs_ratio
            self.m5_rs_ratio = alpha * new_rs + (1 - alpha) * ema_old
            self.ema_history.popleft()
            self.ema_history.append(new_rs)


METRICS_STATE: Dict[str, SymbolMetrics] = {
    s: SymbolMetrics(s) for s in SYMBOLS_TO_WATCH
}
METRICS_STATE[BENCHMARK_SYMBOL] = SymbolMetrics(BENCHMARK_SYMBOL) # BTC jako benchmark

last_price_btc: float = 0.0

# --- Funkcje Pomocnicze ---

def calculate_m2_delta(metrics: SymbolMetrics, current_ts_ms: int):
    # 1. Usuń stare trade'y
    while metrics.trades_buffer and (current_ts_ms - metrics.trades_buffer[0][0] > M2_DELTA_WINDOW_SECONDS * 1000):
        metrics.trades_buffer.popleft()
    
    # 2. Przelicz deltę
    delta = sum(qty * side_sign for _, side_sign, qty in metrics.trades_buffer)
    metrics.m2_delta = delta

async def process_trade(trade_data: Dict[str, Any], cycle_id: str):
    # Trade data from Bybit publicTrade stream: [time, symbol, side, size, price, tradeId, isBuyerMaker, isSelfTrade]
    
    ts_ms = trade_data[0]
    symbol_raw = trade_data[1] # e.g., "BTCUSDT"
    side = trade_data[2] # "Buy" or "Sell"
    qty = float(trade_data[3])
    
    if symbol_raw not in METRICS_STATE:
        return

    metrics = METRICS_STATE[symbol_raw]
    
    # Aktualizacja stanu dla m2_delta
    side_sign = 1 if side == "Buy" else -1
    metrics.trades_buffer.append((ts_ms, side_sign, qty))
    
    # Używamy asyncio.to_thread, aby nie blokować pętli WS, ale obliczenia są szybkie
    await asyncio.to_thread(calculate_m2_delta, metrics, ts_ms)

async def process_ticker(ticker_data: Dict[str, Any], cycle_id: str):
    # Ticker data from Bybit publicTicker stream: [symbol, lastPrice, markPrice, bid1, ask1, ...]
    
    symbol_raw = ticker_data.get("symbol")
    mark_price = float(ticker_data.get("markPrice", 0.0))
    
    if not symbol_raw or mark_price == 0.0:
        return

    if symbol_raw == BENCHMARK_SYMBOL:
        global last_price_btc
        last_price_btc = mark_price
        # Po aktualizacji BTC, musimy przeliczyć RS dla wszystkich
        for sym, metrics in METRICS_STATE.items():
            if sym != BENCHMARK_SYMBOL and metrics.last_price > 0:
                if last_price_btc > 0:
                    rs = metrics.last_price / last_price_btc
                    metrics.update_ema_rs(rs)
        return

    if symbol_raw in METRICS_STATE:
        metrics = METRICS_STATE[symbol_raw]
        metrics.last_price = mark_price
        
        # Aktualizacja RS, jeśli BTC jest znane
        if last_price_btc > 0:
            rs = mark_price / last_price_btc
            metrics.update_ema_rs(rs)


# --- WebSocket Connection Handler ---
async def websocket_listener():
    # W tym miejscu musiałby być zaimplementowany kod do łączenia się z Bybit WS
    # i subskrybowania 'publicTrade' oraz 'tickers' dla wszystkich symboli + BTCUSDT.
    # Ze względu na złożoność pełnej implementacji WS, zakładamy, że dane są
    # asynchronicznie dostarczane do funkcji process_trade/process_ticker.
    logger.info("WebSocket listener started (Placeholder).")
    
    # Symulacja odbierania danych (w prawdziwym kodzie to byłby pętla while True z await session.recv())
    await asyncio.sleep(3600) # Utrzymujemy połączenie aktywne

# --- FastAPI Endpoints ---
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Uruchomienie nasłuchiwania WS w tle
    asyncio.create_task(websocket_listener())
    # Można też pobrać początkowe ceny z REST API (get_latest_prices)

@app.get("/metrics")
async def get_metrics(symbol: str):
    # Symbol bez .P, np. ADAUSDT
    if symbol not in METRICS_STATE:
        raise HTTPException(status_code=404, detail="Symbol not tracked.")
    
    metrics = METRICS_STATE[symbol]
    
    # Weryfikacja, czy stan jest świeży (np. ostatnia aktualizacja < 10s temu)
    # ... (pominięto w szkicu)
    
    return {
        "symbol": metrics.symbol,
        "m2_delta": metrics.m2_delta,
        "m5_rs_ratio": metrics.m5_rs_ratio,
        "as_of": datetime.now(timezone.utc).isoformat()
    }

# Endpoint do weryfikacji stanu (dla health check)
@app.get("/health")
async def health_check():
    return {"status": "healthy", "ws_connected": True} # W prawdziwym kodzie sprawdzić stan WS