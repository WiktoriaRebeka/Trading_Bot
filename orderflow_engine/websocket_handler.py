# orderflow_engine/websocket_handler.py
import logging
import asyncio
import json
import os
import hmac
import hashlib
from typing import Dict, Any, List, Callable
from datetime import datetime, timezone

from aiohttp import ClientSession, ClientError, WSMsgType
from asyncio import TimeoutError

from .config_symbols import ALL_SYMBOLS_FOR_WS, SYMBOLS_TO_WATCH_CLEAN, BENCHMARK_SYMBOL
from .metrics_processor import OrderFlowMetrics

logger = logging.getLogger(__name__)

# --- Konfiguracja API / WS ---

# Public linear stream (USDT perpetual / futures) – mainnet
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

# REST do pobrania początkowej ceny BTC (dla RS)
BYBIT_REST_URL = "https://api.bybit.com/v5"


async def fetch_initial_prices(session: ClientSession, metrics_processor: OrderFlowMetrics) -> bool:
    """
    Pobiera początkową cenę BTC z REST API, aby zainicjalizować RS.
    """
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
                mark_price = float(data["result"]["list"][0].get("markPrice", 0.0))
                if mark_price > 0:
                    metrics_processor.process_ticker(BENCHMARK_SYMBOL, mark_price)
                    logger.info(f"Początkowa cena BTC zainicjalizowana: {mark_price}")
                    return True
            logger.error(f"Nieprawidłowa odpowiedź REST przy pobieraniu tickera BTC: {data}")
            return False
    except (ClientError, TimeoutError, KeyError, TypeError) as e:
        logger.error(f"Błąd pobierania początkowej ceny BTC: {e}")
        return False


def build_subscribe_payload(stream_type: str, symbols: List[str]) -> Dict[str, Any]:
    """
    Buduje payload subskrypcji zgodny z Bybit v5:
    - publicTrade.SYMBOL
    - tickers.SYMBOL
    """
    topics = [f"{stream_type}.{s}" for s in symbols]
    return {
        "op": "subscribe",
        "args": topics,
    }


async def websocket_listener(
    metrics_processor: OrderFlowMetrics,
    process_trade_func: Callable[[int, str, str, float], None],
    process_ticker_func: Callable[[str, float], None],
) -> None:
    """
    Główna pętla WS:
    - reconnect w nieskończoność
    - subskrypcja publicTrade.* i tickers.*
    - parsowanie danych zgodnie z Bybit v5
    """
    while True:
        try:
            logger.info(f"Próba połączenia z Bybit WS: {BYBIT_WS_URL}")
            async with ClientSession() as session:
                # Inicjalizacja ceny BTC dla RS
                await fetch_initial_prices(session, metrics_processor)

                # Utrzymujemy połączenie; heartbeat na poziomie protokołu zapewnia aiohttp
                async with session.ws_connect(
                    BYBIT_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    logger.info("Połączenie WS nawiązane.")

                    # Subskrypcje – zgodne z dokumentacją Bybit v5
                    trade_payload = build_subscribe_payload("publicTrade", SYMBOLS_TO_WATCH_CLEAN)
                    ticker_payload = build_subscribe_payload("tickers", ALL_SYMBOLS_FOR_WS)

                    await ws.send_json(trade_payload)
                    await ws.send_json(ticker_payload)
                    logger.info("Wysłano subskrypcje dla publicTrade.* i tickers.*.")

                    # Pętla nasłuchu
                    async for message in ws:
                        if message.type == WSMsgType.TEXT:
                            try:
                                data = json.loads(message.data)
                            except json.JSONDecodeError:
                                logger.warning(f"Otrzymano niepoprawny JSON z WS: {message.data}")
                                continue

                            op = data.get("op")
                            topic = data.get("topic", "")

                            # Odpowiedzi kontrolne (auth/subscribe/ping) – dla public WS głównie subscribe/ping
                            if op == "subscribe" and data.get("success") is True:
                                logger.debug("Subskrypcja potwierdzona.")
                                continue
                            if op == "ping":
                                # Bybit może odesłać 'op': 'ping' jako potwierdzenie
                                logger.debug("Odebrano PING/PONG z serwera.")
                                continue

                            # Dane z topic
                            if topic.startswith("publicTrade."):
                                # publicTrade payload: data: [ { T, s, S, v, ... }, ... ]
                                for trade in data.get("data", []):
                                    try:
                                        ts_ms = int(trade["T"])
                                        symbol_raw = trade["s"]
                                        side = trade["S"]  # "Buy" / "Sell"
                                        qty = float(trade["v"])
                                        process_trade_func(ts_ms, symbol_raw, side, qty)
                                    except (KeyError, TypeError, ValueError) as e:
                                        logger.warning(f"Błąd parsowania trade z WS: {trade} ({e})")
                                        continue

                            elif topic.startswith("tickers."):
                                # tickers payload: data: [ { symbol, markPrice, ... }, ... ]
                                for ticker in data.get("data", []):
                                    try:
                                        symbol_raw = ticker.get("symbol")
                                        if not symbol_raw:
                                            continue
                                        mark_price = float(ticker.get("markPrice", 0.0))
                                        process_ticker_func(symbol_raw, mark_price)
                                    except (TypeError, ValueError) as e:
                                        logger.warning(f"Błąd parsowania tickera z WS: {ticker} ({e})")
                                        continue

                        elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                            logger.warning(f"Połączenie WS zakończone: {message.type}. Próbuję reconnect za 5s.")
                            break

        except (ClientError, TimeoutError, OSError) as e:
            logger.error(f"Błąd komunikacji WS: {type(e).__name__}. Reconnect za 5s.")
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd w pętli WS: {e}", exc_info=True)

        await asyncio.sleep(5)