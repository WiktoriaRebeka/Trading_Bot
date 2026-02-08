# orderflow_engine/websocket_handler.py

import asyncio
import aiohttp
import json
import logging
import time
from aiohttp import WSMsgType, ClientSession
from typing import List, Dict
from collections import defaultdict
from orderflow_engine.config_symbols import ALL_SYMBOLS_FOR_WS
from orderflow_engine.metrics_processor import OrderFlowMetrics

# integracja: wywołanie procesu decyzyjnego (signal_detector integration)
from orderflow_engine.integration import process_tick_and_maybe_alert

logger = logging.getLogger(__name__)

# Konfiguracja
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
MAX_TOPICS_PER_CONNECTION = 8  # Bezpieczny limit (Bybit pozwala 10, ale zostawiamy bufor)
RECONNECT_DELAY_SECONDS = 5


class OrderBookThrottler:
    """
    Limituje przetwarzanie OrderBook do max 1 raz na sekundę per symbol.
    Oszczędza CPU i koszty Firestore.
    """
    def __init__(self, throttle_seconds: float = 1.0):
        self.last_processed: Dict[str, float] = {}
        self.throttle_seconds = throttle_seconds

    def should_process(self, symbol: str) -> bool:
        """Zwraca True jeśli można przetworzyć OrderBook dla tego symbolu"""
        now = time.time()
        last = self.last_processed.get(symbol, 0)

        if now - last >= self.throttle_seconds:
            self.last_processed[symbol] = now
            return True

        return False


class MultiConnectionWSManager:
    """
    Zarządza wieloma połączeniami WebSocket do Bybit.

    Dlaczego wiele połączeń?
    - Bybit limit: max 10 topics per connection
    - Każdy symbol = 4 topics (trade, ticker, liquidation, orderbook)
    - 20 symboli = 80 topics = potrzeba 10 połączeń
    """

    def __init__(self, symbols: List[str], metrics_processor):
        self.symbols = symbols
        self.processor = metrics_processor
        self.connections = []
        self.ob_throttler = OrderBookThrottler(throttle_seconds=1.0)
        self.is_running = True

        logger.info(f"Inicjalizacja WebSocket Managera dla {len(symbols)} symboli")

    async def start_all_connections(self):
        """
        Uruchamia wszystkie potrzebne połączenia WebSocket.
        Dzieli symbole na grupy (2 symbole per connection = 8 topics).
        """
        symbols_per_connection = 2  # 2 symbole × 4 topics = 8 topics (bezpieczne)

        connection_tasks = []

        for i in range(0, len(self.symbols), symbols_per_connection):
            batch = self.symbols[i:i + symbols_per_connection]

            # Utwórz task dla tej grupy symboli
            task = asyncio.create_task(
                self._maintain_connection_for_batch(batch, connection_id=i // symbols_per_connection)
            )
            connection_tasks.append(task)

        logger.info(f"✅ Uruchomiono {len(connection_tasks)} połączeń WebSocket")

        # Czekaj na wszystkie połączenia (infinite loop)
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        """
        Utrzymuje pojedyncze połączenie WS dla grupy symboli.
        Automatyczny reconnect w przypadku rozłączenia.
        """
        while self.is_running:
            try:
                logger.info(f"[Conn-{connection_id}] Łączenie z Bybit WS dla {symbols_batch}...")
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Błąd połączenia: {e}. Reconnect za {RECONNECT_DELAY_SECONDS}s...")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        """
        Główna pętla WebSocket dla grupy symboli.
        """
        async with ClientSession() as session:
            async with session.ws_connect(
                BYBIT_WS_URL,
                heartbeat=20,
                autoping=True,
                timeout=aiohttp.ClientTimeout(total=None)
            ) as ws:

                # Subskrybuj wszystkie potrzebne topics dla tej grupy
                topics = self._build_topics_for_symbols(symbols_batch)

                subscribe_payload = {
                    "op": "subscribe",
                    "args": topics
                }

                await ws.send_json(subscribe_payload)
                logger.info(f"[Conn-{connection_id}] ✅ Subskrybowano {len(topics)} topics: {symbols_batch}")

                # Główna pętla nasłuchiwania
                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(message.data)

                            # Pomiń wiadomości systemowe (pong, subscribe confirmation)
                            if "op" in data:
                                if data["op"] == "pong":
                                    continue
                                elif data["op"] == "subscribe":
                                    logger.debug(f"[Conn-{connection_id}] Potwierdzenie subskrypcji: {data.get('success')}")
                                    continue

                            # Przetwórz dane rynkowe
                            await self._process_message(data, connection_id)

                        except json.JSONDecodeError as e:
                            logger.warning(f"[Conn-{connection_id}] Błąd JSON: {e}")
                        except Exception as e:
                            logger.error(f"[Conn-{connection_id}] Błąd przetwarzania: {e}", exc_info=True)

                    elif message.type == WSMsgType.ERROR:
                        logger.error(f"[Conn-{connection_id}] WebSocket error: {message}")
                        break

                    elif message.type == WSMsgType.CLOSED:
                        logger.warning(f"[Conn-{connection_id}] Połączenie zamknięte przez serwer")
                        break

    def _build_topics_for_symbols(self, symbols: List[str]) -> List[str]:
        """
        Buduje listę topics dla podanych symboli.
        Każdy symbol = 4 topics.
        """
        topics = []
        for symbol in symbols:
            topics.append(f"publicTrade.{symbol}")      # Delta calculation
            topics.append(f"tickers.{symbol}")          # Price, FR, OI
            topics.append(f"liquidation.{symbol}")      # Stop hunts (PALIWO)
            topics.append(f"orderbook.50.{symbol}")     # DOM analysis (OBRONA)

        return topics

    async def _process_message(self, data: dict, connection_id: int):
        """
        Routing wiadomości do odpowiednich procesorów w metrics_processor.
        Po przetworzeniu kluczowych danych (ticker/orderbook) uruchamiamy
        asynchronicznie proces decyzyjny (signal_detector integration).
        """
        topic = data.get("topic", "")

        if not topic:
            return

        # ========================================
        # 1. PUBLIC TRADES (Delta calculation)
        # ========================================
        if topic.startswith("publicTrade."):
            trades_data = data.get("data", [])

            for trade in trades_data:
                try:
                    self.processor.process_trade(
                        timestamp=int(trade["T"]),
                        symbol=trade["s"],
                        side=trade["S"],          # 'Buy' or 'Sell'
                        qty=float(trade["v"]),
                        price=float(trade["p"])
                    )
                except Exception:
                    logger.exception("Error processing trade")

        # ========================================
        # 2. LIQUIDATIONS (Stop Hunt Detection)
        # ========================================
        elif topic.startswith("liquidation."):
            liq_data = data.get("data", {})

            if not liq_data:
                return

            # Bybit format: data może być dict lub list
            if isinstance(liq_data, list):
                liq_events = liq_data
            else:
                liq_events = [liq_data]

            for liq in liq_events:
                try:
                    self.processor.process_liquidation({
                        'symbol': liq.get('symbol'),
                        'side': 'LONG' if liq.get('side') == 'Buy' else 'SHORT',  # normalizacja
                        'price': float(liq.get('price', 0)),
                        'qty': float(liq.get('size', 0)),
                        'time': int(liq.get('updatedTime', time.time() * 1000))
                    })
                except Exception:
                    logger.exception("Error processing liquidation")

        # ========================================
        # 3. ORDERBOOK L50 (DOM / OBI Analysis)
        # ========================================
        elif topic.startswith("orderbook."):
            ob_raw = data.get("data", {})

            if not ob_raw:
                return

            symbol = ob_raw.get('s')

            # Throttling: przetwarzaj max raz na sekundę per symbol
            if not self.ob_throttler.should_process(symbol):
                return

            # Konwertuj do format [(price, qty), ...]
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
                    'sequence': ob_raw.get('seq', 0)  # Sequence number dla update tracking
                })
            except Exception:
                logger.exception("Error processing orderbook")

            # Po przetworzeniu orderbooku możemy spróbować uruchomić decyzję.
            # Pobieramy aktualną cenę/funding/tick_size z metrics_processor (jeśli dostępne).
            try:
                price = self.processor.get_last_price(symbol)
                funding = self.processor.get_last_funding(symbol)
                tick_size = self.processor.get_tick_size(symbol)
            except Exception:
                # Jeśli brak metod, ustaw fallbacky i kontynuuj
                price = None
                funding = 0.0
                tick_size = 0.0

            if price is not None:
                # Uruchamiamy asynchronicznie proces decyzyjny (nie blokujemy pętli WS)
                try:
                    asyncio.create_task(process_tick_and_maybe_alert(symbol, price, tick_size, funding))
                except Exception:
                    logger.exception("Failed to schedule process_tick_and_maybe_alert after orderbook")

        # ========================================
        # 4. TICKERS (Price, Funding Rate, OI)
        # ========================================
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

            # Po przetworzeniu tickera uruchamiamy decyzję (jeśli mamy price)
            try:
                tick_size = self.processor.get_tick_size(symbol)
            except Exception:
                tick_size = 0.0

            # Nie blokujemy pętli — schedule task
            try:
                asyncio.create_task(process_tick_and_maybe_alert(symbol, price, tick_size, funding_rate))
            except Exception:
                logger.exception("Failed to schedule process_tick_and_maybe_alert after ticker")

        else:
            logger.debug(f"[Conn-{connection_id}] Nieznany topic: {topic}")

    async def shutdown(self):
        """Graceful shutdown wszystkich połączeń"""
        logger.info("Zamykanie wszystkich połączeń WebSocket...")
        self.is_running = False

        # Daj czas na zakończenie pętli
        await asyncio.sleep(2)

        logger.info("✅ Wszystkie połączenia zamknięte")


# ========================================
# LEGACY WRAPPER (dla kompatybilności z istniejącym kodem)
# ========================================

async def websocket_listener(metrics_processor, symbols: List[str]):
    """
    Wrapper dla starego interfejsu.
    Używaj MultiConnectionWSManager zamiast tego bezpośrednio.
    """
    manager = MultiConnectionWSManager(symbols, metrics_processor)
    await manager.start_all_connections()