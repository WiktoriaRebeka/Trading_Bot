# orderflow_engine/websocket_handler.py
import logging
import asyncio
import json
from typing import Dict, Any, List, Callable, Optional

from aiohttp import ClientSession, ClientError, WSMsgType
from asyncio import TimeoutError

from .config_symbols import ALL_SYMBOLS_FOR_WS, SYMBOLS_TO_WATCH_CLEAN, BENCHMARK_SYMBOL
from .metrics_processor import OrderFlowMetrics

logger = logging.getLogger(__name__)

# Public linear stream (USDT perpetual / futures) – mainnet
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_REST_URL = "https://api.bybit.com/v5"

async def fetch_initial_prices(session: ClientSession, metrics_processor: OrderFlowMetrics) -> bool:
    """Pobiera początkową cenę BTC z REST API."""
    if metrics_processor.last_price_btc != 0.0:
        return True

    logger.info("Pobieranie początkowej ceny BTC z REST API...")
    endpoint = f"{BYBIT_REST_URL}/market/tickers"
    params = {"category": "linear", "symbol": BENCHMARK_SYMBOL}

    try:
        async with session.get(endpoint, params=params, timeout=10) as response:
            response.raise_for_status()
            data = await response.json()

            if data.get("retCode") == 0 and data.get("result", {}).get("list"):
                ticker_data = data["result"]["list"][0]
                mark_price = float(ticker_data.get("markPrice", 0.0))
                
                # V2.1: Pobieramy też initial FR i OI dla BTC (opcjonalnie)
                fr = float(ticker_data.get("fundingRate", 0.0))
                oi = float(ticker_data.get("openInterest", 0.0))

                if mark_price > 0:
                    metrics_processor.process_ticker(BENCHMARK_SYMBOL, mark_price, fr, oi)
                    logger.info(f"Początkowa cena BTC zainicjalizowana: {mark_price}")
                    return True
            return False
    except Exception as e:
        logger.error(f"Błąd pobierania początkowej ceny BTC: {e}")
        return False

def build_subscribe_payload(stream_type: str, symbols: List[str]) -> Dict[str, Any]:
    topics = [f"{stream_type}.{s}" for s in symbols]
    return {"op": "subscribe", "args": topics}

async def websocket_listener(
    metrics_processor: OrderFlowMetrics,
    process_trade_func: Callable[[int, str, str, float], None],
    process_ticker_func: Callable[[str, float, Optional[float], Optional[float]], None],
) -> None:
    """
    Główna pętla WS.
    V2.1: Parsuje fundingRate i openInterest z tickera.
    """
    while True:
        try:
            logger.info(f"Próba połączenia z Bybit WS: {BYBIT_WS_URL}")
            async with ClientSession() as session:
                await fetch_initial_prices(session, metrics_processor)

                async with session.ws_connect(BYBIT_WS_URL, heartbeat=20, autoping=True) as ws:
                    logger.info("Połączenie WS nawiązane.")

                    trade_payload = build_subscribe_payload("publicTrade", SYMBOLS_TO_WATCH_CLEAN)
                    ticker_payload = build_subscribe_payload("tickers", ALL_SYMBOLS_FOR_WS)

                    await ws.send_json(trade_payload)
                    await ws.send_json(ticker_payload)

                    async for message in ws:
                        if message.type == WSMsgType.TEXT:
                            try:
                                data = json.loads(message.data)
                            except json.JSONDecodeError:
                                continue

                            topic = data.get("topic", "")

                            # --- publicTrade.* ---
                            if topic.startswith("publicTrade."):
                                for trade in data.get("data", []):
                                    try:
                                        process_trade_func(
                                            int(trade["T"]), 
                                            trade["s"], 
                                            trade["S"], 
                                            float(trade["v"])
                                        )
                                    except Exception:
                                        continue
                                        
                            # --- tickers.* ---
                            elif topic.startswith("tickers."):
                                ticker = data.get("data")
                                if not isinstance(ticker, dict): continue

                                try:
                                    symbol_raw = ticker.get("symbol")
                                    if not symbol_raw: continue

                                    # V2.1: Parsowanie rozszerzonych danych
                                    # Bybit wysyła tylko zmienione pola, więc używamy .get()
                                    mark_price = float(ticker.get("markPrice", 0.0)) if "markPrice" in ticker else 0.0
                                    
                                    funding_rate = None
                                    if "fundingRate" in ticker:
                                        funding_rate = float(ticker["fundingRate"])
                                        
                                    open_interest = None
                                    if "openInterest" in ticker:
                                        open_interest = float(ticker["openInterest"])

                                    process_ticker_func(symbol_raw, mark_price, funding_rate, open_interest)

                                except Exception:
                                    continue

                        elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                            break

        except Exception as e:
            logger.error(f"[WS-ERROR] {e}. Próba reconnect za 5s.")
        
        await asyncio.sleep(5)