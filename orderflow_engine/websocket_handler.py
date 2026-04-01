# orderflow_engine/websocket_handler.py
import asyncio
import json
import logging
import os
import random
import threading
import time
import websockets
from typing import List, Dict

from orderflow_engine.metrics_processor import OrderFlowMetrics
from orderflow_engine.integration import evaluate_and_maybe_alert

logger = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_MIN_WS_OPEN_TIMEOUT_SEC = float(os.environ.get("WS_OPEN_TIMEOUT_FLOOR_SEC", "45"))
_ws_open_timeout_clamp_logged = False

def _ws_open_timeout_seconds() -> float:
    global _ws_open_timeout_clamp_logged
    try:
        t = float(os.environ.get("WS_OPEN_TIMEOUT_SEC", "75"))
    except (TypeError, ValueError):
        t = 75.0
    if t < _MIN_WS_OPEN_TIMEOUT_SEC:
        if not _ws_open_timeout_clamp_logged:
            logger.warning("WS_OPEN_TIMEOUT_SEC=%s poniżej minimum %ss — ustawiam %ss.", t, _MIN_WS_OPEN_TIMEOUT_SEC, _MIN_WS_OPEN_TIMEOUT_SEC)
            _ws_open_timeout_clamp_logged = True
        return _MIN_WS_OPEN_TIMEOUT_SEC
    return t

RECONNECT_DELAY_SECONDS = 5
EVALUATION_THROTTLE_SEC = 1.0 

class OrderBookThrottler:
    def __init__(self, throttle_seconds: float = 1.0):
        self.last_processed: Dict[str, float] = {}
        self.throttle_seconds = throttle_seconds

    def should_process(self, symbol: str) -> bool:
        sym = str(symbol).upper()
        now = time.time()
        last = self.last_processed.get(sym, 0)
        if now - last >= self.throttle_seconds:
            self.last_processed[sym] = now
            return True
        return False

class MultiConnectionWSManager:
    SYMBOLS_PER_WS_BATCH = 10

    def __init__(self, symbols: List[str], metrics_processor):
        self.symbols = symbols
        self.processor = metrics_processor
        self.is_running = True
        self._ob_delta_min_sec = float(os.environ.get("WS_ORDERBOOK_DELTA_MIN_SEC", "0.35"))
        self.ob_delta_throttler = OrderBookThrottler(throttle_seconds=self._ob_delta_min_sec)
        self.ob_eval_throttler = OrderBookThrottler(throttle_seconds=1.0)
        self._last_evaluation_time: Dict[str, float] = {}
        self._eval_throttle_lock = threading.Lock()
        self._msg_count = 0
        self._last_telemetry_time = time.time()
        self._ws_handshake_ok: set[int] = set()
        n_conn = (len(self.symbols) + self.SYMBOLS_PER_WS_BATCH - 1) // self.SYMBOLS_PER_WS_BATCH
        self._subscribe_confirmed: Dict[int, asyncio.Event] = {i: asyncio.Event() for i in range(n_conn)}

    def connection_count(self) -> int:
        return len(self._subscribe_confirmed)

    async def wait_until_subscriptions_confirmed(self, timeout: float) -> bool:
        events = list(self._subscribe_confirmed.values())
        if not events: return True
        try:
            await asyncio.wait_for(asyncio.gather(*[e.wait() for e in events]), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _mark_subscribe_confirmed(self, connection_id: int) -> None:
        ev = self._subscribe_confirmed.get(connection_id)
        if ev is not None and not ev.is_set():
            ev.set()

    async def start_all_connections(self):
        spc = self.SYMBOLS_PER_WS_BATCH
        connection_tasks = []
        for i in range(0, len(self.symbols), spc):
            batch = self.symbols[i : i + spc]
            task = asyncio.create_task(self._maintain_connection_for_batch(batch, i // spc))
            connection_tasks.append(task)
            await asyncio.sleep(0.5)
        
        logger.info(f"✅ Uruchomiono {len(connection_tasks)} workerów WebSocket")
        await asyncio.gather(*connection_tasks)

    async def _maintain_connection_for_batch(self, symbols_batch: List[str], connection_id: int):
        delay = RECONNECT_DELAY_SECONDS
        while self.is_running:
            try:
                await self._websocket_listener_for_batch(symbols_batch, connection_id)
                delay = RECONNECT_DELAY_SECONDS
            except Exception as e:
                logger.error(f"[Conn-{connection_id}] Błąd pętli: {e}. Reconnect za {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _websocket_listener_for_batch(self, symbols_batch: List[str], connection_id: int):
        try:
            use_long_stagger = (connection_id == 0) or (len(self._ws_handshake_ok) == 0)
            if use_long_stagger:
                stagger = float(os.environ.get("WS_CONN_STAGGER_SEC", "0.45"))
                cap = float(os.environ.get("WS_CONN_STAGGER_CAP_SEC", "12"))
                jitter = float(os.environ.get("WS_CONN_STAGGER_JITTER_SEC", "0.9"))
                wait_s = min(connection_id * stagger, cap) + random.uniform(0, max(0.0, jitter))
            else:
                base = float(os.environ.get("WS_RECONNECT_JITTER_BASE_SEC", "2.0"))
                wait_s = random.uniform(0.5, base) + min(connection_id * 0.2, 6.0)
            if wait_s > 0: await asyncio.sleep(wait_s)

            async with websockets.connect(BYBIT_WS_URL, open_timeout=_ws_open_timeout_seconds()) as ws:
                self._ws_handshake_ok.add(connection_id)
                topics = [f"{t}.{s}" for s in symbols_batch for t in ["publicTrade", "tickers", "orderbook.50", "allLiquidation"]]
                await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                
                async def _ping():
                    while self.is_running:
                        await ws.send(json.dumps({"op": "ping"}))
                        await asyncio.sleep(20)
                
                ping_task = asyncio.create_task(_ping())
                try:
                    async for raw_message in ws:
                        data = json.loads(raw_message)
                        if "op" in data and data.get("op") == "subscribe" and data.get("success"):
                            self._mark_subscribe_confirmed(connection_id)
                        elif "topic" in data:
                            await self._process_message(data, connection_id)
                finally:
                    ping_task.cancel()
        except Exception as e:
            logger.error(f"[Conn-{connection_id}] Błąd: {e}")
            raise

    async def _process_message(self, data: dict, connection_id: int):
        """Główny punkt wejścia dla danych z giełdy."""
        topic: str = data.get("topic", "")
        payload = data.get("data")
        if not payload:
            return

        self._msg_count += 1
        now = time.time()
        if now - self._last_telemetry_time > 30:
            logger.info(f"📊 TELEMETRIA: Przetworzono {self._msg_count} komunikatów w 30s.")
            self._msg_count = 0
            self._last_telemetry_time = now

        symbol = topic.split('.')[-1] if '.' in topic else "unknown"

        # 1. PUBLIC TRADES
        if topic.startswith("publicTrade."):
            for t in payload:
                try:
                    raw_t = t.get("T", 0)
                    ts = int(float(raw_t)) if raw_t not in (None, "") else int(time.time() * 1000)
                    self.processor.process_trade(
                        timestamp=ts,
                        symbol=t.get("s") or t.get("symbol", ""),
                        side=t.get("S") or t.get("side", ""),
                        qty=float(t.get("v", 0) or 0),
                        price=float(t.get("p", 0) or 0),
                    )
                except Exception as e:
                    logger.warning(f"[Conn-{connection_id}] Błąd przetwarzania ticku: {e} | dane: {t}")
                    continue

        # 2. LIQUIDATIONS (v5: allLiquidation.{symbol}, pola T,s,S,v,p)
        elif topic.startswith("allLiquidation."):
            items = payload if isinstance(payload, list) else [payload]
            for liq in items:
                liq_symbol = liq.get("s") or liq.get("symbol", "unknown")
                raw_t = liq.get("T", liq.get("updatedTime", time.time() * 1000))
                try:
                    t_ms = int(float(raw_t))
                except (TypeError, ValueError):
                    t_ms = int(time.time() * 1000)
                try:
                    px = float(liq.get("p", liq.get("price", 0)) or 0)
                    qty = float(liq.get("v", liq.get("size", 0)) or 0)
                except (TypeError, ValueError):
                    logger.warning(f"[Conn-{connection_id}] Liq skip {liq_symbol}: bad p/v in {liq}")
                    continue
                self.processor.process_liquidation({
                    'symbol': liq_symbol,
                    'side': liq.get("S") or liq.get("side"),
                    'price': px,
                    'qty': qty,
                    'time': t_ms,
                })
                await self._trigger_evaluation(liq_symbol)

        # 3. ORDERBOOK — snapshot zawsze (stan początkowy); delty throttlowane (merge jest CPU‑ciężki).
        # Przetwarzanie KAŻDEJ delty na pętli asyncio zatykało async for ws → telemetria spadała do kilku msg/30s.
        elif topic.startswith("orderbook."):
            ob_type = str(data.get("type") or "snapshot").lower()
            if ob_type == "delta" and not self.ob_delta_throttler.should_process(symbol):
                return
            try:
                bids_parsed = [(float(p), float(q)) for p, q in payload.get("b", [])]
                asks_parsed = [(float(p), float(q)) for p, q in payload.get("a", [])]
            except (TypeError, ValueError) as e:
                logger.warning(
                    f"[Conn-{connection_id}] Orderbook parse skip {symbol}: {e} topic={topic!r}"
                )
            else:
                ts_raw = data.get("ts") or payload.get("ts") or payload.get("u")
                try:
                    ts_ob = int(float(ts_raw)) if ts_raw not in (None, "") else int(time.time() * 1000)
                except (TypeError, ValueError):
                    ts_ob = int(time.time() * 1000)
                self.processor.process_orderbook({
                    "symbol": symbol,
                    "bids": bids_parsed,
                    "asks": asks_parsed,
                    "timestamp": ts_ob,
                    "msg_type": ob_type,
                })
                if self.ob_eval_throttler.should_process(symbol):
                    await self._trigger_evaluation(symbol)

        # 4. TICKERS (V5: snapshot + delta — brak pola = bez zmiany; Bybit: lastPrice, markPrice, …)
        elif topic.startswith("tickers."):
            pl = payload
            if isinstance(pl, list):
                if len(pl) == 1 and isinstance(pl[0], dict):
                    pl = pl[0]
                else:
                    logger.warning(
                        f"[Conn-{connection_id}] tickers.{symbol}: nieobsługiwany kształt data=list len={len(pl)}"
                    )
                    return
            if not isinstance(pl, dict):
                logger.warning(
                    f"[Conn-{connection_id}] tickers.{symbol}: data nie jest dict, type={type(pl).__name__}"
                )
                return

            def _f(key: str) -> float | None:
                v = pl.get(key)
                if v is None or v == "":
                    return None
                try:
                    x = float(v)
                    return x if x > 0 else None
                except (TypeError, ValueError):
                    return None

            price = _f("lastPrice") or _f("markPrice")
            if price is None:
                bp, ap = _f("bid1Price"), _f("ask1Price")
                if bp is not None and ap is not None:
                    price = (bp + ap) / 2.0
            if price is None:
                price = 0.0

            sym_u = str(symbol).upper()
            prev = self.processor.tickers.get(sym_u, {})

            def _merge_float(key: str, prev_key: str) -> float:
                if key not in pl:
                    return float(prev.get(prev_key, 0.0) or 0.0)
                v = pl.get(key)
                if v is None or v == "":
                    return float(prev.get(prev_key, 0.0) or 0.0)
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return float(prev.get(prev_key, 0.0) or 0.0)

            funding_rate = _merge_float("fundingRate", "funding_rate")
            open_interest = _merge_float("openInterest", "open_interest")
            volume_24h = _merge_float("volume24h", "volume_24h")

            self.processor.process_ticker(
                symbol=symbol,
                price=price,
                funding_rate=funding_rate,
                open_interest=open_interest,
                volume_24h=volume_24h,
            )
            await self._trigger_evaluation(symbol)

    async def _trigger_evaluation(self, symbol: str):
        """Uruchamia silnik decyzyjny z dławieniem (throttle). Lock zapobiega lawinie tasków z równoległych wiadomości WS."""
        sym = str(symbol).upper()
        with self._eval_throttle_lock:
            now = time.time()
            if now - self._last_evaluation_time.get(sym, 0) < EVALUATION_THROTTLE_SEC:
                return
            self._last_evaluation_time[sym] = now
        asyncio.create_task(self._safe_evaluate(sym))

    async def _safe_evaluate(self, symbol: str):
        """Bezpieczne wywołanie analizy sygnału."""
        sym = str(symbol).upper()
        try:
            await evaluate_and_maybe_alert(sym, self.processor)
        except Exception as e:
            logger.error(f"❌ Błąd ewaluacji dla {sym}: {e}")

    async def shutdown(self):
        """Zamknięcie managera."""
        self.is_running = False
        await asyncio.sleep(1)

async def websocket_listener(metrics_processor, symbols: List[str]):
    """Funkcja startowa wywoływana przez main.py."""
    manager = MultiConnectionWSManager(symbols, metrics_processor)
    await manager.start_all_connections()
