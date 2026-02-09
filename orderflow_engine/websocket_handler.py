# orderflow_engine/websocket_handler.py

import asyncio
import aiohttp
import json
import logging
import time
from aiohttp import WSMsgType, ClientSession
from typing import List, Dict

from orderflow_engine.config_symbols import ALL_SYMBOLS_FOR_WS
from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.integration import evaluate_and_maybe_alert

logger = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
MAX_TOPICS_PER_CONNECTION = 8
RECONNECT_DELAY_SECONDS = 5


class OrderBookThrottler:
    def __init__(self, throttle_seconds: float = 1.0):
        self.last_processed: Dict[str, float] = {}
        self.throttle_seconds = throttle_seconds

    def should_process(self, symbol: str) -> bool:
        now = time.time()
        last = self.last_processed.get(symbol, 0)

        if now - last >= self.throttle_seconds:
            self.last_processed[symbol] = now
            return True

        return False


class MultiConnectionWSManager:
    def __init__(self, symbols: List[str], metrics_processor):
        self.symbols = symbols
        self.processor = metrics_processor
        self.connections = []
        self.ob_throttler = OrderBookThrottler(throttle_seconds=1.0)
        self.is_running = True

        logger.info(f"Inicjalizacja WebSocket Managera dla {len(symbols)} symboli")

    async def start_all_connections(self):
        symbols_per_connection = 2
        connection_tasks = []

        for i in range(0, len(self.symbols), symbols_per_connection):
            batch = self.symbols[i:i + symbols_per_connection]

            task = asyncio.create_task(
                self._maintain_connection_for_batch(batch, connection_id=i // symbols_per_connection)
            )
            connection_tasks.append(task)

        logger.info(f"Uruchomiono {len(connection_tasks)} połączeń WebSocket")
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        while self.is_running:
            try:
                logger.info(f"[Conn-{connection_id}] Łączenie z Bybit WS dla {symbols_batch}...")
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Błąd połączenia: {e}. Reconnect za {RECONNECT_DELAY_SECONDS}s...")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        async with ClientSession() as session:
            async with session.ws_connect(
                BYBIT_WS_URL,
                heartbeat=20,
                autoping=True,
                timeout=aiohttp.ClientTimeout(total=None)
            ) as ws:

                topics = self._build_topics_for_symbols(symbols_batch)
                await ws.send_json({"op": "subscribe", "args": topics})

                logger.info(f"[Conn-{connection_id}] Subskrybowano {len(topics)} topics: {symbols_batch}")

                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(message.data)

                            if "op" in data:
                                if data["op"] == "pong":
                                    continue
                                elif data["op"] == "subscribe":
                                    continue

                            await self._process_message(data, connection_id)

                        except Exception as e:
                            logger.error(f"[Conn-{connection_id}] Błąd przetwarzania: {e}", exc_info=True)

                    elif message.type == WSMsgType.ERROR:
                        logger.error(f"[Conn-{connection_id}] WebSocket error: {message}")
                        break

                    elif message.type == WSMsgType.CLOSED:
                        logger.warning(f"[Conn-{connection_id}] Połączenie zamknięte przez serwer")
                        break

    def _build_topics_for_symbols(self, symbols: List[str]) -> List[str]:
        topics = []
        for symbol in symbols:
            topics.append(f"publicTrade.{symbol}")
            topics.append(f"tickers.{symbol}")
            topics.append(f"liquidation.{symbol}")
            topics.append(f"orderbook.50.{symbol}")
        return topics

    async def _process_message(self, data: dict, connection_id: int):
        topic = data.get("topic", "")
        if not topic:
            return

        # ============================
        # 1. PUBLIC TRADES
        # ============================
        if topic.startswith("publicTrade."):
            trades_data = data.get("data", [])
            for trade in trades_data:
                try:
                    self.processor.process_trade(
                        timestamp=int(trade["T"]),
                        symbol=trade["s"],
                        side=trade["S"],
                        qty=float(trade["v"]),
                        price=float(trade["p"])
                    )
                except Exception:
                    logger.exception("Error processing trade")

        # ============================
        # 2. LIQUIDATIONS
        # ============================
        elif topic.startswith("liquidation."):
            liq_data = data.get("data", {})
            if not liq_data:
                return

            liq_events = liq_data if isinstance(liq_data, list) else [liq_data]

            for liq in liq_events:
                try:
                    self.processor.process_liquidation({
                        'symbol': liq.get('symbol'),
                        'side': 'LONG' if liq.get('side') == 'Buy' else 'SHORT',
                        'price': float(liq.get('price', 0)),
                        'qty': float(liq.get('size', 0)),
                        'time': int(liq.get('updatedTime', time.time() * 1000))
                    })
                except Exception:
                    logger.exception("Error processing liquidation")

        # ============================
        # 3. ORDERBOOK
        # ============================
        elif topic.startswith("orderbook."):
            ob_raw = data.get("data", {})
            if not ob_raw:
                return

            symbol = ob_raw.get('s')

            if not self.ob_throttler.should_process(symbol):
                return

            try:
                bids = [(float(p), float(q)) for p, q in ob_raw.get('b', [])]
                asks = [(float(p), float(q)) for p, q in ob_raw.get('a', [])]
            except Exception:
                logger.exception("Malformed orderbook data")
                return

            try:
                self.processor.process_orderbook({
                    'symbol': symbol,
                    'bids': bids,
                    'asks': asks,
                    'timestamp': int(ob_raw.get('u', time.time() * 1000)),
                    'sequence': ob_raw.get('seq', 0)
                })
            except Exception:
                logger.exception("Error processing orderbook")

            # 🔥 WYWOŁANIE MODUŁU DECYZYJNEGO
            asyncio.create_task(evaluate_and_maybe_alert(symbol, self.processor))

        # ============================
        # 4. TICKERS
        # ============================
        elif topic.startswith("tickers."):
            ticker = data.get("data", {})
            if not ticker:
                return

            try:
                symbol = ticker.get("symbol")
                price = float(ticker.get('markPrice', 0))
                funding_rate = float(ticker.get('fundingRate', 0))
                open_interest = float(ticker.get('openInterest', 0))
                volume_24h = float(ticker.get('volume24h', 0))

                self.processor.process_ticker(
                    symbol=symbol,
                    price=price,
                    funding_rate=funding_rate,
                    open_interest=open_interest,
                    volume_24h=volume_24h
                )
            except Exception:
                logger.exception("Error processing ticker")

            # 🔥 WYWOŁANIE MODUŁU DECYZYJNEGO
            asyncio.create_task(evaluate_and_maybe_alert(symbol, self.processor))

        else:
            logger.debug(f"[Conn-{connection_id}] Nieznany topic: {topic}")

    async def shutdown(self):
        logger.info("Zamykanie wszystkich połączeń WebSocket...")
        self.is_running = False
        await asyncio.sleep(2)
        logger.info("Wszystkie połączenia zamknięte")


async def websocket_listener(metrics_processor, symbols: List[str]):
    manager = MultiConnectionWSManager(symbols, metrics_processor)
    await manager.start_all_connections()