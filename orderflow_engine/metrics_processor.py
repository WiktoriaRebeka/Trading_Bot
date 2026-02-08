import time
import logging
import requests
import json
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime, timezone

# Importy lokalne
from orderflow_engine.market_structure import MarketStructureEngine 
from orderflow_engine.candle_builder import CandleBuilder

logger = logging.getLogger(__name__)

# ========================================
# MODELE DANYCH
# ========================================

@dataclass
class LiquidationEvent:
    symbol: str
    side: str          # 'Buy' = Long liquidation, 'Sell' = Short liquidation
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

# ========================================
# GŁÓWNY PROCESOR METRYK
# ========================================

class OrderFlowMetrics:
    def __init__(self, firestore_client=None):
        self.firestore = firestore_client
        
        # --- Silniki i Budowniczy ---
        self.builders = defaultdict(lambda: CandleBuilder(""))
        self.engines = defaultdict(lambda: MarketStructureEngine(lookback_bars=500))
        
        # --- Bufory danych ---
        self.trades = defaultdict(lambda: deque(maxlen=2000))
        self.tickers = {}
        self.liquidations = defaultdict(list)
        self.orderbook_snapshots = {}
        self.delta_history = defaultdict(lambda: deque(maxlen=100))
        
        # --- Konfiguracja progów ---
        self.LIQUIDATION_CASCADE_THRESHOLD_USD = 50000
        self.DOM_WALL_MULTIPLIER = 3.0
        self.SIGNAL_COOLDOWN_SEC = 300 # 5 minut przerwy między sygnałami na tym samym symbolu
        self.last_signal_time = defaultdict(float)

        logger.info("✅ OrderFlowMetrics V6.0 zainicjowany (Autonomous Hunter Mode)")

    # ========================================
    # 1. PRE-LOAD (BACKFILL)
    # ========================================
    def pre_load_history(self, symbol, history_m1, history_d1):
        engine = self.engines[symbol]
        if history_d1:
            engine.major_high = max([k['high'] for k in history_d1])
            engine.major_low = min([k['low'] for k in history_d1])
        for c in history_m1:
            engine.update_candles(c['open'], c['high'], c['low'], c['close'], c['ts'])
        logger.info(f"✅ {symbol} Backfill OK. Poziom SL: {engine.last_swing_low}")

    # ========================================
    # 2. PRZETWARZANIE DANYCH LIVE
    # ========================================
    def process_trade(self, timestamp: int, symbol: str, side: str, qty: float, price: float):
        self.trades[symbol].append({'timestamp': timestamp, 'side': side, 'qty': qty, 'price': price})
        
        # Buduj świecę 1m
        builder = self.builders[symbol]
        if not builder.symbol: builder.symbol = symbol
        new_candle = builder.process_tick(price, qty, timestamp)
        
        # Jeśli świeca się zamknęła, zaktualizuj silnik struktury
        if new_candle:
            self.engines[symbol].update_candles(
                new_candle['open'], new_candle['high'], 
                new_candle['low'], new_candle['close'], new_candle['ts']
            )
        
        # Aktualizuj historię delty
        delta = self._calculate_delta_window(symbol, 60)
        self.delta_history[symbol].append({'price': price, 'delta': delta, 'timestamp': timestamp})

    def process_ticker(self, symbol, price, funding_rate, open_interest, volume_24h):
        self.tickers[symbol] = {
            'price': price, 
            'funding_rate': funding_rate, 
            'open_interest': open_interest
        }
        # Uruchom skaner autonomiczny przy każdym ticku ceny
        self._autonomous_scanner(symbol)

    def process_liquidation(self, liq):
        event = LiquidationEvent(
            liq['symbol'], liq['side'], liq['price'], 
            liq['qty'], liq['time'], liq['qty'] * liq['price']
        )
        self.liquidations[event.symbol].append(event)
        # Usuń starsze niż 60s
        cutoff = int(time.time() * 1000) - 60000
        self.liquidations[event.symbol] = [e for e in self.liquidations[event.symbol] if e.time > cutoff]

    def process_orderbook(self, ob_data: dict):
        symbol = ob_data['symbol']
        bids, asks = ob_data['bids'], ob_data['asks']
        if not bids or not asks: return

        # Oblicz OBI
        bid_vol = sum([p * q for p, q in bids[:10]])
        ask_vol = sum([p * q for p, q in asks[:10]])
        obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0

        # Wykryj ściany
        bid_walls = self._detect_walls(bids, 'bid')
        ask_walls = self._detect_walls(asks, 'ask')

        self.orderbook_snapshots[symbol] = DOMSnapshot(
            symbol=symbol, bids=bids[:10], asks=asks[:10],
            timestamp=ob_data['timestamp'], obi=obi,
            best_bid=bids[0][0], best_ask=asks[0][0],
            bid_walls=bid_walls, ask_walls=ask_walls
        )

    # ========================================
    # 3. SKANER I LOGIKA DECYZYJNA
    # ========================================
    def _autonomous_scanner(self, symbol):
        data = self.tickers.get(symbol)
        if not data: return
        
        price = data['price']
        engine = self.engines[symbol]
        
        # Rate limit na sygnały
        if time.time() - self.last_signal_time[symbol] < self.SIGNAL_COOLDOWN_SEC:
            return

        # LAYER 1: Market Structure Sweep
        if engine.is_sweep_happening(price):
            direction = "LONG" if price < (engine.last_swing_low or 0) else "SHORT"
            self._validate_setup(symbol, direction, price)

    def _validate_setup(self, symbol, direction, price):
        # LAYER 2: Likwidacje (Paliwo)
        recent_liqs = self.liquidations.get(symbol, [])
        liq_vol = sum([l.value_usd for l in recent_liqs])
        
        if liq_vol < self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            return

        # LAYER 3: Dywergencja Delty
        div = self._get_delta_divergence_logic(symbol)
        if not div['detected']:
            return

        # LAYER 4: DOM Validation (OBI)
        dom = self.orderbook_snapshots.get(symbol)
        if not dom: return
        
        obi_ok = (direction == "LONG" and dom.obi > 0.4) or (direction == "SHORT" and dom.obi < -0.4)
        
        if obi_ok:
            # ★★★ WSZYSTKIE FILTRY PRZESZŁY -> WYKONAJ ★★★
            self.last_signal_time[symbol] = time.time()
            entry_price = dom.best_bid if direction == "LONG" else dom.best_ask
            self._execute_signal(symbol, direction, entry_price)

    # ========================================
    # 4. EGZEKUCJA (Wysyłka do bota)
    # ========================================
    def _execute_signal(self, symbol, direction, level):
        event_id = f"PY-{symbol}-{int(time.time())}"
        
        payload = {
            "event_id": event_id,
            "signal_id": f"AUTO_{symbol}_{direction}",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "direction": direction,
            "entry": level,
            "sl": level * 0.994 if direction == "LONG" else level * 1.006, # 0.6% SL
            "tp": level * 1.018 if direction == "LONG" else level * 0.982, # 1.8% TP (RR 3.0)
            "risk_pct": 0.6,
            "rr": 3.0,
            "structure_state": 1 if direction == "LONG" else -1,
            "risk_usdt": 10.0 # Ryzyko 10 USD na trade
        }

        BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"
        
        try:
            # Wysyłka asynchroniczna byłaby lepsza, ale requests.post w tym kontekście jest akceptowalny
            response = requests.post(BOT_SERVICE_URL, json=payload, timeout=5)
            if response.status_code == 200:
                logger.warning(f"🚀 SYGNAŁ WYSŁANY: {symbol} {direction} na {level}")
            else:
                logger.error(f"❌ Bot Service Error: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"❌ Krytyczny błąd wysyłki sygnału: {e}")

    # ========================================
    # NARZĘDZIA POMOCNICZE
    # ========================================
    def _calculate_delta_window(self, symbol: str, seconds: int) -> float:
        cutoff = int(time.time() * 1000) - (seconds * 1000)
        recent = [t for t in self.trades[symbol] if t['timestamp'] > cutoff]
        buy_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Buy'])
        sell_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Sell'])
        return buy_v - sell_v

    def _detect_walls(self, levels: List[Tuple[float, float]], side: str) -> List[OrderBookWall]:
        if not levels: return []
        avg_size = sum([q for _, q in levels[:20]]) / 20
        threshold = avg_size * self.DOM_WALL_MULTIPLIER
        walls = []
        for p, q in levels[:20]:
            if q > threshold:
                walls.append(OrderBookWall(price=p, size=q, distance_from_mid=0, side=side))
        return walls

    def _get_delta_divergence_logic(self, symbol: str) -> dict:
        hist = list(self.delta_history.get(symbol, []))
        if len(hist) < 10: return {'detected': False}
        curr, prev = hist[-1], hist[-5]
        # Bullish Divergence: Cena niżej, Delta wyżej
        if curr['price'] < prev['price'] and curr['delta'] > prev['delta']:
            return {'detected': True, 'type': 'BULLISH'}
        # Bearish Divergence: Cena wyżej, Delta niżej
        if curr['price'] > prev['price'] and curr['delta'] < prev['delta']:
            return {'detected': True, 'type': 'BEARISH'}
        return {'detected': False}

    def get_full_context(self, symbol: str) -> dict:
        """Endpoint API dla Bot Service"""
        engine = self.engines[symbol]
        ticker = self.tickers.get(symbol, {})
        return {
            "price": ticker.get('price'),
            "last_swing_low": engine.last_swing_low,
            "last_swing_high": engine.last_swing_high,
            "major_low": engine.major_low,
            "major_high": engine.major_high,
            "funding_rate": ticker.get('funding_rate'),
            "open_interest": ticker.get('open_interest')
        }