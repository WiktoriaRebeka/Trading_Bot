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
EVALUATION_THROTTLE_SEC = 1.0  # Nie sprawdzaj sygnału częściej niż raz na sekundę dla symbolu

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
        
        # Słownik do dławienia (throttlingu) modułu decyzyjnego
        self._last_evaluation_time: Dict[str, float] = {}

        logger.info(f"🚀 Inicjalizacja MultiConnectionWSManager | Symbole: {len(symbols)}")

    async def start_all_connections(self):
        # Bybit pozwala na wiele tematów, ale dla stabilności trzymamy małe paczki
        symbols_per_connection = 2 
        connection_tasks = []

        for i in range(0, len(self.symbols), symbols_per_connection):
            batch = self.symbols[i:i + symbols_per_connection]
            task = asyncio.create_task(
                self._maintain_connection_for_batch(batch, connection_id=i // symbols_per_connection)
            )
            connection_tasks.append(task)

        logger.info(f"✅ Uruchomiono {len(connection_tasks)} workerów WebSocket")
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        while self.is_running:
            try:
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Krytyczny błąd pętli: {e}. Reconnect za {RECONNECT_DELAY_SECONDS}s")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        async with ClientSession() as session:
            async with session.ws_connect(
                BYBIT_WS_URL,
                heartbeat=20,
                autoping=True
            ) as ws:

                topics = self._build_topics_for_symbols(symbols_batch)
                await ws.send_json({"op": "subscribe", "args": topics})
                logger.info(f"[Conn-{connection_id}] Subskrypcja aktywna dla: {symbols_batch}")

                async for message in ws:
                    if not self.is_running: break

                    if message.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(message.data)
                            if "topic" in data:
                                await self._process_message(data, connection_id)
                        except Exception as e:
                            logger.error(f"[Conn-{connection_id}] Błąd dekodowania JSON: {e}")

                    elif message.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        logger.warning(f"[Conn-{connection_id}] Połączenie przerwane (Type: {message.type})")
                        break

    def _build_topics_for_symbols(self, symbols: List[str]) -> List[str]:
        topics = []
        for s in symbols:
            topics.extend([f"publicTrade.{s}", f"tickers.{s}", f"liquidation.{s}", f"orderbook.50.{s}"])
        return topics

    async def _trigger_evaluation(self, symbol: str):
        """
        Decyduje, czy uruchomić silnik decyzyjny (Throttling).
        Zapobiega przeciążeniu CPU przy 50 symbolach.
        """
        now = time.time()
        last_eval = self._last_evaluation_time.get(symbol, 0)

        if now - last_eval >= EVALUATION_THROTTLE_SEC:
            self._last_evaluation_time[symbol] = now
            # Uruchamiamy jako niezależne zadanie, by nie blokować odczytu z WS
            asyncio.create_task(self._safe_evaluate(symbol))

    async def _safe_evaluate(self, symbol: str):
        """Bezpieczne opakowanie dla modułu decyzyjnego."""
        try:
            await evaluate_and_maybe_alert(symbol, self.processor)
        except Exception as e:
            logger.error(f"[EVAL-ERROR] Błąd w evaluate_and_maybe_alert dla {symbol}: {e}")

    async def _process_message(self, data: dict, connection_id: int):
        topic: str = data.get("topic", "")
        symbol = topic.split('.')[-1] if '.' in topic else "unknown"

        # 1. PUBLIC TRADES
        if topic.startswith("publicTrade."):
            trades = data.get("data", [])
            for t in trades:
                try:
                    self.processor.process_trade(
                        timestamp=int(t["T"]), symbol=t["s"], side=t["S"],
                        qty=float(t["v"]), price=float(t["p"])
                    )
                except Exception as e:
                    logger.error(f"[WS-FLOW] Trade Error {symbol}: {e}")
            
            # Trade'y są bardzo częste - nie triggerujemy ewaluacji tutaj, 
            # czekamy na Ticker lub Orderbook dla oszczędności CPU.

        # 2. LIQUIDATIONS
        elif topic.startswith("liquidation."):
            liq = data.get("data", {})
            if liq:
                try:
                    self.processor.process_liquidation({
                        'symbol': liq.get('symbol'),
                        'side': 'LONG' if liq.get('side') == 'Buy' else 'SHORT',
                        'price': float(liq.get('price', 0)),
                        'qty': float(liq.get('size', 0)),
                        'time': int(liq.get('updatedTime', time.time() * 1000))
                    })
                    logger.info(f"[WS-FLOW] 💧 Liquidation detected: {symbol}")
                    # Likwidacja to ważny event - wymuszamy sprawdzenie sygnału
                    await self._trigger_evaluation(symbol)
                except Exception as e:
                    logger.error(f"[WS-FLOW] Liq Error {symbol}: {e}")

        # 3. ORDERBOOK
        elif topic.startswith("orderbook."):
            if self.ob_throttler.should_process(symbol):
                ob_raw = data.get("data", {})
                try:
                    self.processor.process_orderbook({
                        'symbol': symbol,
                        'bids': [(float(p), float(q)) for p, q in ob_raw.get('b', [])],
                        'asks': [(float(p), float(q)) for p, q in ob_raw.get('a', [])],
                        'timestamp': int(ob_raw.get('u', time.time() * 1000))
                    })
                    # Trigger ewaluacji po aktualizacji arkusza
                    await self._trigger_evaluation(symbol)
                except Exception as e:
                    logger.error(f"[WS-FLOW] OB Error {symbol}: {e}")

        # 4. TICKERS
        elif topic.startswith("tickers."):
            t = data.get("data", {})
            try:
                self.processor.process_ticker(
                    symbol=symbol,
                    price=float(t.get('markPrice', 0)),
                    funding_rate=float(t.get('fundingRate', 0)),
                    open_interest=float(t.get('openInterest', 0)),
                    volume_24h=float(t.get('volume24h', 0))
                )
                # Ticker przychodzi co ~100-200ms, idealny moment na trigger
                await self._trigger_evaluation(symbol)
            except Exception as e:
                logger.error(f"[WS-FLOW] Ticker Error {symbol}: {e}")

    async def shutdown(self):
        logger.info("🛑 Zamykanie WebSocket Managera...")
        self.is_running = False
        await asyncio.sleep(1)