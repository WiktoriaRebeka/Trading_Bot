# orderflow_engine/websocket_handler.py
import asyncio
import aiohttp
import json
import logging
import time
from aiohttp import WSMsgType, ClientSession
from typing import List, Dict

from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.integration import evaluate_and_maybe_alert

logger = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
RECONNECT_DELAY_SECONDS = 5
EVALUATION_THROTTLE_SEC = 1.0 

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
        self.is_running = True
        self.ob_throttler = OrderBookThrottler(throttle_seconds=1.0)
        self._last_evaluation_time: Dict[str, float] = {}
        
        # TELEMETRIA
        self._msg_count = 0
        self._last_telemetry_time = time.time()

    async def start_all_connections(self):
        symbols_per_connection = 2 
        connection_tasks = []
        for i in range(0, len(self.symbols), symbols_per_connection):
            batch = self.symbols[i:i + symbols_per_connection]
            task = asyncio.create_task(self._maintain_connection_for_batch(batch, i // symbols_per_connection))
            connection_tasks.append(task)
        logger.info(f"✅ Uruchomiono {len(connection_tasks)} workerów WebSocket")
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        while self.is_running:
            try:
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Błąd: {e}. Reconnect za {RECONNECT_DELAY_SECONDS}s")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        async with ClientSession() as session:
            async with session.ws_connect(BYBIT_WS_URL, heartbeat=20, autoping=True) as ws:
                topics = []
                for s in symbols_batch:
                    topics.extend([f"publicTrade.{s}", f"tickers.{s}", f"liquidation.{s}", f"orderbook.50.{s}"])
                
                await ws.send_json({"op": "subscribe", "args": topics})
                logger.info(f"[Conn-{connection_id}] WYSYŁAM SUBSKRYPCJĘ dla {symbols_batch}")

                async for message in ws:
                    if not self.is_running: break
                    if message.type == WSMsgType.TEXT:
                        data = json.loads(message.data)
                        if "op" in data and data.get("success") is True:
                            logger.info(f"[Conn-{connection_id}] Subskrypcja POTWIERDZONA")
                            continue
                        if "topic" in data:
                            await self._process_message(data, connection_id)
                    elif message.type in (WSMsgType.CLOSED, WSMsgType.ERROR): break

    async def _process_message(self, data: dict, connection_id: int):
        # Telemetria
        self._msg_count += 1
        now = time.time()
        if now - self._last_telemetry_time > 30:
            logger.info(f"📊 TELEMETRIA: Przetworzono {self._msg_count} komunikatów w 30s.")
            self._msg_count = 0
            self._last_telemetry_time = now

        topic: str = data.get("topic", "")
        symbol = topic.split('.')[-1]

        if topic.startswith("publicTrade."):
            for t in data.get("data", []):
                self.processor.process_trade(int(t["T"]), t["s"], t["S"], float(t["v"]), float(t["p"]))
        elif topic.startswith("liquidation."):
            liq = data.get("data", {})
            if liq:
                self.processor.process_liquidation({
                    'symbol': liq.get('symbol'), 'side': 'LONG' if liq.get('side') == 'Buy' else 'SHORT',
                    'price': float(liq.get('price', 0)), 'qty': float(liq.get('size', 0)),
                    'time': int(liq.get('updatedTime', time.time() * 1000))
                })
        elif topic.startswith("orderbook."):
            if self.ob_throttler.should_process(symbol):
                ob = data.get("data", {})
                self.processor.process_orderbook({
                    'symbol': symbol, 'bids': [(float(p), float(q)) for p, q in ob.get('b', [])],
                    'asks': [(float(p), float(q)) for p, q in ob.get('a', [])],
                    'timestamp': int(ob.get('u', time.time() * 1000))
                })
        elif topic.startswith("tickers."):
            t = data.get("data", {})
            self.processor.process_ticker(symbol, float(t.get('markPrice', 0)), float(t.get('fundingRate', 0)), float(t.get('openInterest', 0)), float(t.get('volume24h', 0)))
        
        await self._trigger_evaluation(symbol)

    async def _trigger_evaluation(self, symbol: str):
        now = time.time()
        if now - self._last_evaluation_time.get(symbol, 0) >= EVALUATION_THROTTLE_SEC:
            self._last_evaluation_time[symbol] = now
            asyncio.create_task(self._safe_evaluate(symbol))

    async def _safe_evaluate(self, symbol: str):
        try: await evaluate_and_maybe_alert(symbol, self.processor)
        except Exception as e: logger.error(f"Eval Error {symbol}: {e}")

    async def shutdown(self):
        self.is_running = False
        await asyncio.sleep(1)

async def websocket_listener(metrics_processor, symbols: List[str]):
    manager = MultiConnectionWSManager(symbols, metrics_processor)
    await manager.start_all_connections()