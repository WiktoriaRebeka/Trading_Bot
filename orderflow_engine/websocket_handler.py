# orderflow_engine/websocket_handler.py
import asyncio
import json
import logging
import time
import websockets
from typing import List, Dict

from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.integration import evaluate_and_maybe_alert

logger = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
RECONNECT_DELAY_SECONDS = 5
EVALUATION_THROTTLE_SEC = 1.0 

class OrderBookThrottler:
    """Ogranicza przetwarzanie gęstych danych arkusza zleceń."""
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
        """Uruchamia połączenia WebSocket w paczkach po 2 symbole."""
        symbols_per_connection = 2 
        connection_tasks = []
        for i in range(0, len(self.symbols), symbols_per_connection):
            batch = self.symbols[i:i + symbols_per_connection]
            task = asyncio.create_task(self._maintain_connection_for_batch(batch, i // symbols_per_connection))
            connection_tasks.append(task)
        logger.info(f"✅ Uruchomiono {len(connection_tasks)} workerów WebSocket")
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        """Pętla utrzymująca połączenie (Auto-reconnect)."""
        while self.is_running:
            try:
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Błąd pętli: {e}. Reconnect za {RECONNECT_DELAY_SECONDS}s")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        """Obsługa pojedynczego połączenia WebSocket."""
        try:
            async with websockets.connect(
                BYBIT_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=30,
                close_timeout=10,
            ) as ws:
                topics = []
                for s in symbols_batch:
                    topics.extend([
                        f"publicTrade.{s}",
                        f"tickers.{s}",
                        f"orderbook.50.{s}",
                        f"allLiquidation.{s}",
                    ])

                await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                logger.info(f"[Conn-{connection_id}] WYSYŁAM SUBSKRYPCJĘ ({len(topics)} tematów) dla {symbols_batch}")

                async def _bybit_ping_loop():
                    while self.is_running:
                        try:
                            await asyncio.sleep(20)
                            await ws.send(json.dumps({"op": "ping"}))
                        except Exception:
                            break

                ping_task = asyncio.create_task(_bybit_ping_loop())

                try:
                    async for raw_message in ws:
                        if not self.is_running: break
                        logger.info(f"[Conn-{connection_id}] MSG type={type(raw_message).__name__} len={len(raw_message)}")
                        if isinstance(raw_message, bytes):
                            logger.warning(f"[Conn-{connection_id}] BINARY_MSG len={len(raw_message)}")
                            continue
                        data = json.loads(raw_message)

                        if "op" in data:
                            op = data.get("op")
                            if op == "subscribe":
                                if data.get("success") is True:
                                    logger.info(
                                        f"[Conn-{connection_id}] Subskrypcja POTWIERDZONA dla {symbols_batch}"
                                    )
                                else:
                                    logger.error(
                                        f"[Conn-{connection_id}] Subskrypcja ODRZUCONA: {data}"
                                    )
                            elif op == "ping":
                                pass
                            else:
                                logger.debug(f"[Conn-{connection_id}] Wiadomość sterująca op={op}: {data}")
                            continue

                        if "topic" in data:
                            await self._process_message(data, connection_id)
                finally:
                    ping_task.cancel()
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[Conn-{connection_id}] Połączenie zamknięte przez serwer")
        except Exception as e:
            logger.error(f"[Conn-{connection_id}] Błąd połączenia: {e}")

    async def _process_message(self, data: dict, connection_id: int):
        """Główny punkt wejścia dla danych z giełdy."""
        # --- TELEMETRIA ---
        self._msg_count += 1
        now = time.time()
        if now - self._last_telemetry_time > 30:
            logger.info(f"📊 TELEMETRIA: Przetworzono {self._msg_count} komunikatów w 30s.")
            self._msg_count = 0
            self._last_telemetry_time = now

        topic: str = data.get("topic", "")
        symbol = topic.split('.')[-1] if '.' in topic else "unknown"
        payload = data.get("data")
        if not payload: return

        # 1. PUBLIC TRADES
        if topic.startswith("publicTrade."):
            for t in payload:
                try:
                    self.processor.process_trade(
                        timestamp=int(t["T"]), symbol=t["s"], side=t["S"],
                        qty=float(t["v"]), price=float(t["p"])
                    )
                except Exception as e:
                    logger.warning(f"[Conn-{connection_id}] Błąd przetwarzania ticku: {e} | dane: {t}")
                    continue

        # 2. LIQUIDATIONS (v5: allLiquidation.{symbol}, pola T,s,S,v,p)
        elif topic.startswith("allLiquidation."):
            items = payload if isinstance(payload, list) else [payload]
            for liq in items:
                liq_symbol = liq.get("s") or liq.get("symbol", "unknown")
                self.processor.process_liquidation({
                    'symbol': liq_symbol,
                    'side': liq.get("S") or liq.get("side"),
                    'price': float(liq.get("p", liq.get("price", 0))),
                    'qty': float(liq.get("v", liq.get("size", 0))),
                    'time': int(liq.get("T", liq.get("updatedTime", time.time() * 1000)))
                })
                await self._trigger_evaluation(liq_symbol)

        # 3. ORDERBOOK
        elif topic.startswith("orderbook."):
            if self.ob_throttler.should_process(symbol):
                self.processor.process_orderbook({
                    'symbol': symbol, 
                    'bids': [(float(p), float(q)) for p, q in payload.get('b', [])],
                    'asks': [(float(p), float(q)) for p, q in payload.get('a', [])],
                    'timestamp': int(payload.get('u', time.time() * 1000))
                })
                await self._trigger_evaluation(symbol)

        # 4. TICKERS
        elif topic.startswith("tickers."):
            self.processor.process_ticker(
                symbol=symbol,
                price=float(payload.get('markPrice', 0)),
                funding_rate=float(payload.get('fundingRate', 0)),
                open_interest=float(payload.get('openInterest', 0)),
                volume_24h=float(payload.get('volume24h', 0))
            )
            await self._trigger_evaluation(symbol)

    async def _trigger_evaluation(self, symbol: str):
        """Uruchamia silnik decyzyjny z dławieniem (throttle)."""
        now = time.time()
        if now - self._last_evaluation_time.get(symbol, 0) >= EVALUATION_THROTTLE_SEC:
            self._last_evaluation_time[symbol] = now
            asyncio.create_task(self._safe_evaluate(symbol))

    async def _safe_evaluate(self, symbol: str):
        """Bezpieczne wywołanie analizy sygnału."""
        try:
            await evaluate_and_maybe_alert(symbol, self.processor)
        except Exception as e:
            logger.error(f"❌ Błąd ewaluacji dla {symbol}: {e}")

    async def shutdown(self):
        """Zamknięcie managera."""
        self.is_running = False
        await asyncio.sleep(1)

async def websocket_listener(metrics_processor, symbols: List[str]):
    """Funkcja startowa wywoływana przez main.py."""
    manager = MultiConnectionWSManager(symbols, metrics_processor)
    await manager.start_all_connections()
