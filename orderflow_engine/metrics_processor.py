# orderflow_engine/metrics_processor.py
# WERSJA: 7.1 - ELITE INSTITUTIONAL ENGINE (Full Logic + Ingestion Layer)

import os
import time
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime, timezone

# Importy lokalne
from orderflow_engine.market_structure import MarketStructureEngine 
from orderflow_engine.candle_builder import CandleBuilder
from orderflow_engine.bigquery_logger import OrderFlowBigQueryLogger
from orderflow_engine.confidence_scorer import ConfidenceScorer
from orderflow_engine.integration import update_symbol_context

logger = logging.getLogger(__name__)

@dataclass
class LiquidationEvent:
    symbol: str
    side: str
    price: float
    qty: float
    time: int
    value_usd: float

@dataclass
class OrderBookWall:
    price: float
    size: float
    distance_from_mid: float
    side: str

@dataclass
class DOMSnapshot:
    symbol: str
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]
    timestamp: int
    obi: float
    best_bid: float
    best_ask: float
    bid_walls: List[OrderBookWall]
    ask_walls: List[OrderBookWall]

class OrderFlowMetrics:
    def __init__(self, firestore_client=None):
        self.firestore = firestore_client
        self.builders = defaultdict(lambda: CandleBuilder(""))
        self.engines = defaultdict(lambda: MarketStructureEngine(lookback_bars=500))
        self.bq_logger = OrderFlowBigQueryLogger()
        self.scorer = ConfidenceScorer()
        
        self.trades = defaultdict(lambda: deque(maxlen=20000))
        self.tickers = {}
        self.liquidations = defaultdict(list)
        self.orderbook_snapshots = {}
        self.delta_history = defaultdict(lambda: deque(maxlen=200))
        
        self.LIQUIDATION_CASCADE_THRESHOLD_USD = int(
            os.environ.get("LIQ_CASCADE_THRESHOLD_USD", "10000")
        )
        logger.info(f"[MetricsProcessor] LIQ_CASCADE_THRESHOLD_USD={self.LIQUIDATION_CASCADE_THRESHOLD_USD}")
        self.DOM_WALL_MULTIPLIER = 3.5 
        self.SIGNAL_COOLDOWN_SEC = 300
        self.MIN_CONFIDENCE_SCORE = 75 
        self.last_signal_time = defaultdict(float)

        self.bot_url = os.environ.get(
            "BOT_SERVICE_URL",
            "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"
        )
        logger.info(f"[MetricsProcessor] BOT_SERVICE_URL={'env' if os.environ.get('BOT_SERVICE_URL') else 'fallback'}: {self.bot_url}")

        logger.info("✅ OrderFlow V7.1: Institutional Engine Active.")

    def pre_load_history(self, symbol, history_m1, history_d1):
        engine = self.engines[symbol]
        if history_d1:
            engine.major_high = max([k['high'] for k in history_d1])
            engine.major_low = min([k['low'] for k in history_d1])
        for c in history_m1:
            engine.update_candles(c['open'], c['high'], c['low'], c['close'], c['ts'])

    def process_trade(self, timestamp: int, symbol: str, side: str, qty: float, price: float):
        self.trades[symbol].append({'timestamp': timestamp, 'side': side, 'qty': qty, 'price': price})
        builder = self.builders[symbol]
        if not builder.symbol: builder.symbol = symbol
        new_candle = builder.process_tick(price, qty, timestamp)
        if new_candle:
            self.engines[symbol].update_candles(new_candle['open'], new_candle['high'], new_candle['low'], new_candle['close'], new_candle['ts'])
        delta = self._calculate_delta_window(symbol, 60)
        self.delta_history[symbol].append({'price': price, 'delta': delta, 'timestamp': timestamp})

    def process_ticker(self, symbol, price, funding_rate, open_interest, volume_24h):
        self.tickers[symbol] = {
            'price': price, 'funding_rate': funding_rate, 
            'open_interest': open_interest, 'volume_24h': volume_24h
        }
        # To wywołanie gwarantuje, że bot_service widzi aktualną cenę
        self._refresh_context_cache(symbol) 
        self._autonomous_scanner(symbol)

    def process_liquidation(self, liq):
        event = LiquidationEvent(liq['symbol'], liq['side'], liq['price'], liq['qty'], liq['time'], liq['qty'] * liq['price'])
        self.liquidations[event.symbol].append(event)
        cutoff = int(time.time() * 1000) - 60000
        self.liquidations[event.symbol] = [e for e in self.liquidations[event.symbol] if e.time > cutoff]
        self._check_liquidation_cascade(event.symbol)
        self._refresh_context_cache(event.symbol)

    def process_orderbook(self, ob_data: dict):
        symbol = ob_data['symbol']
        bids, asks = ob_data['bids'], ob_data['asks']
        if not bids or not asks: return
        bid_vol = sum([p * q for p, q in bids[:10]])
        ask_vol = sum([p * q for p, q in asks[:10]])
        obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
        bid_walls = self._detect_walls(bids, 'bid')
        ask_walls = self._detect_walls(asks, 'ask')
        mid_price = (bids[0][0] + asks[0][0]) / 2
        for wall in bid_walls: wall.distance_from_mid = mid_price - wall.price
        for wall in ask_walls: wall.distance_from_mid = wall.price - mid_price
        snapshot = DOMSnapshot(symbol=symbol, bids=bids[:10], asks=asks[:10], timestamp=ob_data['timestamp'], obi=obi, best_bid=bids[0][0], best_ask=asks[0][0], bid_walls=bid_walls, ask_walls=ask_walls)
        self.orderbook_snapshots[symbol] = snapshot
        if bid_walls or ask_walls: self._log_dom_walls_to_bq(symbol, snapshot)
        self._refresh_context_cache(symbol)

    def _refresh_context_cache(self, symbol: str):
        """WARSTWA INGERENCJI: Aktualizuje globalny cache w integration.py"""
        try:
            ticker = self.tickers.get(symbol, {})
            engine = self.engines[symbol]
            dom = self.orderbook_snapshots.get(symbol)
            ctx = {
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "price": ticker.get('price'),
                "funding_rate": ticker.get('funding_rate', 0.0),
                "dom": {"obi": dom.obi if dom else 0.0, "bid_walls": len(dom.bid_walls) if dom else 0, "ask_walls": len(dom.ask_walls) if dom else 0},
                "liquidations": self.get_recent_liquidations(symbol),
                "structure": {"last_swing_high": engine.last_swing_high, "last_swing_low": engine.last_swing_low},
                "delta_points": self.get_recent_deltas(symbol, limit=5)
            }
            update_symbol_context(symbol, ctx)
        except Exception as e:
            logger.error(f"❌ Context Cache Error {symbol}: {e}")

    def _autonomous_scanner(self, symbol):
        data = self.tickers.get(symbol)
        if not data or time.time() - self.last_signal_time[symbol] < self.SIGNAL_COOLDOWN_SEC: return
        price = data['price']
        engine = self.engines[symbol]
        if engine.is_sweep_happening(price):
            direction = "LONG" if price < (engine.last_swing_low or 0) else "SHORT"
            self._validate_setup_layers(symbol, direction, price)

    def _validate_setup_layers(self, symbol, direction, price):
        recent_liqs = self.liquidations.get(symbol, [])
        liq_vol = sum([l.value_usd for l in recent_liqs])
        div = self._detect_delta_divergence(symbol)
        dom = self.orderbook_snapshots.get(symbol)
        confidence = self.scorer.calculate({
            'liquidation_volume_usd': liq_vol, 'delta_divergence': div['detected'], 'delta_strength': div.get('strength', 0),
            'obi': abs(dom.obi) if dom else 0, 'dom_wall_detected': len(dom.bid_walls if direction == "LONG" else dom.ask_walls) > 0 if dom else False,
            'structure_strength': self.engines[symbol].get_swing_strength(), 'funding_rate': self.tickers[symbol].get('funding_rate', 0), 'direction': direction
        })
        if confidence >= self.MIN_CONFIDENCE_SCORE and liq_vol >= self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            self.last_signal_time[symbol] = time.time()
            entry_price = dom.best_bid if (dom and direction == "LONG") else price
            import asyncio
            asyncio.create_task(self._execute_signal_async(symbol, direction, entry_price, confidence, liq_vol, div))

    async def _execute_signal_async(self, symbol: str, direction: str, level: float, score: float, liq_v: float, div: dict):
        import aiohttp
        event_id = f"PY-{symbol}-{int(time.time())}"
        payload = {
            "event_id": event_id, "signal_id": f"AUTO-{event_id}", "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "direction": direction, "entry": level,
            "sl": level * 0.994 if direction == "LONG" else level * 1.006,
            "tp": level * 1.018 if direction == "LONG" else level * 0.982,
            "risk_pct": 0.6, "rr": 3.0, "structure_state": 1 if direction == "LONG" else -1, "risk_usdt": 10.0,
            "raw_context": {"confidence_score": score, "liq_volume_usd": liq_v, "delta_div_detected": div['detected'], "delta_strength": div.get('strength', 0)}
        }
        BOT_URL = self.bot_url
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BOT_URL, json=payload, timeout=5) as resp:
                    if resp.status == 200: logger.warning(f"🚀 SYGNAŁ WYSŁANY: {symbol} {direction} | Score: {score:.1f}")
        except Exception as e: logger.error(f"❌ Błąd komunikacji async: {e}")

    def _calculate_delta_window(self, symbol, seconds):
        cutoff = int(time.time() * 1000) - (seconds * 1000)
        recent = [t for t in self.trades[symbol] if t['timestamp'] > cutoff]
        buy_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Buy'])
        sell_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Sell'])
        return buy_v - sell_v

    def _detect_walls(self, levels, side):
        if not levels or len(levels) < 10: return []
        avg_s = sum([q for _, q in levels[:20]]) / 20
        walls = []
        for p, q in levels[:20]:
            if q > avg_s * self.DOM_WALL_MULTIPLIER: walls.append(OrderBookWall(p, q, 0, side))
        return walls

    def _detect_delta_divergence(self, symbol):
        hist = list(self.delta_history[symbol])
        if len(hist) < 30: return {'detected': False}
        recent = hist[-30:]; prices = [d['price'] for d in recent]; deltas = [d['delta'] for d in recent]
        if prices[-1] < min(prices[-10:-1]) and deltas[-1] > min(deltas[-10:-1]): return {'detected': True, 'type': 'BULLISH', 'strength': abs(deltas[-1] - min(deltas[-10:-1]))}
        if prices[-1] > max(prices[-10:-1]) and deltas[-1] < max(deltas[-10:-1]): return {'detected': True, 'type': 'BEARISH', 'strength': abs(deltas[-1] - max(deltas[-10:-1]))}
        return {'detected': False}

    def _check_liquidation_cascade(self, symbol):
        liqs = self.liquidations.get(symbol, [])
        if not liqs: return
        total = sum([e.value_usd for e in liqs])
        if total > self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            self.bq_logger.log_liquidation_cascade({'event_id': f"LIQ-{int(time.time())}", 'symbol': symbol, 'cascade_type': 'LONG_CASCADE' if sum([e.value_usd for e in liqs if e.side=='Buy']) > total*0.7 else 'SHORT_CASCADE', 'total_volume_usd': total, 'count': len(liqs)})

    def _log_dom_walls_to_bq(self, symbol, snapshot):
        for wall in snapshot.bid_walls + snapshot.ask_walls:
            if wall.distance_from_mid / wall.price < 0.005: self.bq_logger.log_dom_wall({'symbol': symbol, 'side': wall.side.upper(), 'price': wall.price, 'size': wall.size, 'obi': snapshot.obi})

    def get_last_price(self, symbol: str) -> Optional[float]:
        t = self.tickers.get(symbol); return t['price'] if t else None

    def get_last_funding(self, symbol: str) -> float:
        t = self.tickers.get(symbol); return t.get('funding_rate', 0.0) if t else 0.0

    def get_recent_liquidations(self, symbol: str, window_sec: int = 60):
        now_ms = int(time.time() * 1000); cutoff = now_ms - (window_sec * 1000); liqs = self.liquidations.get(symbol, [])
        return [{
            'side': e.side,
            'volume_usd': e.value_usd,
            'timestamp': datetime.fromtimestamp(e.time / 1000, tz=timezone.utc).isoformat(),
            'T': str(e.time),
            'time': e.time,
        } for e in liqs if e.time >= cutoff]
    def get_recent_deltas(self, symbol: str, limit: int = 10):
        hist = list(self.delta_history[symbol])[-limit:]
        return [{
            'price': d['price'], 
            'delta': d['delta'], 
            'timestamp': datetime.fromtimestamp(d['timestamp'] / 1000, tz=timezone.utc).isoformat() # FIX: ISO string
        } for d in hist]
    def get_dom_snapshot(self, symbol: str):
        snap = self.orderbook_snapshots.get(symbol)
        if not snap: return {'bids': [], 'asks': [], 'obi': 0.0}
        return {'bids': snap.bids, 'asks': snap.asks, 'obi': snap.obi}

    def get_full_context(self, symbol):
        engine = self.engines[symbol]
        return {"symbol": symbol, "structure": {"last_swing_high": engine.last_swing_high, "last_swing_low": engine.last_swing_low}, "dom": {"obi": self.orderbook_snapshots[symbol].obi if symbol in self.orderbook_snapshots else 0}, "ticker": self.tickers.get(symbol)}