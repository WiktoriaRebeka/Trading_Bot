# orderflow_engine/metrics_processor.py
# WERSJA: 6.0 - PRODUCTION READY

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
from orderflow_engine.bigquery_logger import OrderFlowBigQueryLogger
from orderflow_engine.confidence_scorer import ConfidenceScorer

logger = logging.getLogger(__name__)

# ========================================
# MODELE DANYCH
# ========================================

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
        
        # --- NOWE: BigQuery Logger i Confidence Scorer ---
        self.bq_logger = OrderFlowBigQueryLogger()
        self.scorer = ConfidenceScorer()
        
        # --- Konfiguracja progów ---
        self.LIQUIDATION_CASCADE_THRESHOLD_USD = 50000
        self.DOM_WALL_MULTIPLIER = 3.0
        self.SIGNAL_COOLDOWN_SEC = 300  # 5 minut
        self.MIN_CONFIDENCE_SCORE = 70  # Minimum 70/100
        self.last_signal_time = defaultdict(float)

        logger.info("✅ OrderFlowMetrics V6.0 zainicjowany (BigQuery + Confidence Scoring)")

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
        logger.info(f"✅ {symbol} Backfill OK. Swing Low: {engine.last_swing_low}")

    # ========================================
    # 2. PRZETWARZANIE DANYCH LIVE
    # ========================================
    def process_trade(self, timestamp: int, symbol: str, side: str, qty: float, price: float):
        self.trades[symbol].append({'timestamp': timestamp, 'side': side, 'qty': qty, 'price': price})
        
        # Buduj świecę 1m
        builder = self.builders[symbol]
        if not builder.symbol: 
            builder.symbol = symbol
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
            'open_interest': open_interest,
            'volume_24h': volume_24h
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
        
        # Sprawdź kaskadę i loguj do BigQuery
        self._check_liquidation_cascade(event.symbol)

    def process_orderbook(self, ob_data: dict):
        symbol = ob_data['symbol']
        bids, asks = ob_data['bids'], ob_data['asks']
        if not bids or not asks: 
            return

        # Oblicz OBI
        bid_vol = sum([p * q for p, q in bids[:10]])
        ask_vol = sum([p * q for p, q in asks[:10]])
        obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0

        # Wykryj ściany
        bid_walls = self._detect_walls(bids, 'bid')
        ask_walls = self._detect_walls(asks, 'ask')

        # Utwórz snapshot
        mid_price = (bids[0][0] + asks[0][0]) / 2
        
        for wall in bid_walls:
            wall.distance_from_mid = mid_price - wall.price
        
        for wall in ask_walls:
            wall.distance_from_mid = wall.price - mid_price

        snapshot = DOMSnapshot(
            symbol=symbol, bids=bids[:10], asks=asks[:10],
            timestamp=ob_data['timestamp'], obi=obi,
            best_bid=bids[0][0], best_ask=asks[0][0],
            bid_walls=bid_walls, ask_walls=ask_walls
        )
        
        self.orderbook_snapshots[symbol] = snapshot
        
        # Loguj ściany do BigQuery (tylko znaczące)
        if bid_walls or ask_walls:
            self._log_dom_walls(symbol, snapshot)

    # ========================================
    # 3. SKANER I LOGIKA DECYZYJNA
    # ========================================
    def _autonomous_scanner(self, symbol):
        data = self.tickers.get(symbol)
        if not data: 
            return
        
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
        """
        Waliduje setup przez wszystkie 4 warstwy i oblicza Confidence Score.
        """
        
        # LAYER 2: Likwidacje (Paliwo)
        recent_liqs = self.liquidations.get(symbol, [])
        liq_vol = sum([l.value_usd for l in recent_liqs])
        
        if liq_vol < self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            logger.debug(f"❌ {symbol} Rejected: Liq volume {liq_vol:.0f} < {self.LIQUIDATION_CASCADE_THRESHOLD_USD}")
            return

        # LAYER 3: Dywergencja Delty
        div = self._detect_delta_divergence(symbol)
        
        if not div['detected']:
            logger.debug(f"❌ {symbol} Rejected: No delta divergence")
            return
        
        # Sprawdź czy typ dywergencji pasuje do kierunku
        if direction == "LONG" and div['type'] != 'BULLISH':
            logger.debug(f"❌ {symbol} Rejected: Direction mismatch (want LONG, got {div['type']})")
            return
        
        if direction == "SHORT" and div['type'] != 'BEARISH':
            logger.debug(f"❌ {symbol} Rejected: Direction mismatch (want SHORT, got {div['type']})")
            return

        # LAYER 4: DOM Validation (OBI)
        dom = self.orderbook_snapshots.get(symbol)
        if not dom: 
            logger.debug(f"❌ {symbol} Rejected: No DOM data")
            return
        
        # Wymagamy silnej przewagi w DOM
        obi_threshold = 0.3
        obi_ok = (direction == "LONG" and dom.obi > obi_threshold) or \
                 (direction == "SHORT" and dom.obi < -obi_threshold)
        
        if not obi_ok:
            logger.debug(f"❌ {symbol} Rejected: OBI {dom.obi:.2f} not strong enough (need {obi_threshold})")
            return
        
        # ========================================
        # WSZYSTKIE FILTRY PRZESZŁY -> OBLICZ CONFIDENCE
        # ========================================
        engine = self.engines[symbol]
        ticker_data = self.tickers[symbol]
        
        confidence = self.scorer.calculate({
            'liquidation_volume_usd': liq_vol,
            'delta_divergence': True,
            'delta_strength': div.get('strength', 0),
            'obi': abs(dom.obi),
            'dom_wall_detected': len(dom.bid_walls if direction == "LONG" else dom.ask_walls) > 0,
            'structure_strength': engine.get_swing_strength(),
            'funding_rate': ticker_data.get('funding_rate', 0),
            'direction': direction
        })
        
        logger.info(f"🎯 {symbol} Setup validated. Confidence: {confidence:.1f}/100")
        
        # Sprawdź minimum threshold
        if confidence < self.MIN_CONFIDENCE_SCORE:
            logger.warning(f"⚠️ {symbol} Rejected: Confidence {confidence:.1f} < {self.MIN_CONFIDENCE_SCORE}")
            return
        
        # ★★★ CONFIDENCE WYSTARCZAJĄCO WYSOKI -> WYKONAJ ★★★
        self.last_signal_time[symbol] = time.time()
        entry_price = dom.best_bid if direction == "LONG" else dom.best_ask
        
        self._execute_signal(
            symbol=symbol, 
            direction=direction, 
            entry_price=entry_price,
            confidence=confidence,
            metadata={
                'liq_volume': liq_vol,
                'delta_strength': div['strength'],
                'obi': dom.obi,
                'wall_detected': len(dom.bid_walls if direction == "LONG" else dom.ask_walls) > 0,
                'funding_rate': ticker_data.get('funding_rate', 0)
            }
        )

    # ========================================
    # 4. EGZEKUCJA (Wysyłka do bota)
    # ========================================
    def _execute_signal(self, symbol, direction, entry_price, confidence, metadata):
        event_id = f"PY-{symbol}-{int(time.time())}"
        
        # Oblicz SL i TP
        sl_distance_pct = 0.006  # 0.6% SL
        tp_distance_pct = 0.018  # 1.8% TP (RR 3.0)
        
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_distance_pct)
            tp_price = entry_price * (1 + tp_distance_pct)
        else:
            sl_price = entry_price * (1 + sl_distance_pct)
            tp_price = entry_price * (1 - tp_distance_pct)
        
        payload = {
            "event_id": event_id,
            "signal_id": f"AUTO_{symbol}_{direction}",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "direction": direction,
            "entry": entry_price,
            "sl": sl_price,
            "tp": tp_price,
            "risk_pct": sl_distance_pct * 100,
            "rr": 3.0,
            "structure_state": 1 if direction == "LONG" else -1,
            "risk_usdt": 10.0,  # Ryzyko 10 USD na trade
            
            # Metadata (opcjonalne dla bot_service, ale ważne dla BigQuery)
            "funding_rate": metadata.get('funding_rate', 0),
            "open_interest": self.tickers[symbol].get('open_interest', 0)
        }
        
        # KROK 1: Loguj setup do BigQuery (PRZED wysłaniem do bota)
        try:
            self.bq_logger.log_setup_signal({
                'setup_id': f"SETUP-{event_id}",
                'event_id': event_id,
                'symbol': symbol,
                'direction': direction,
                'entry': entry_price,
                'sl': sl_price,
                'tp': tp_price,
                'liq_volume': metadata['liq_volume'],
                'delta_div': True,
                'delta_strength': metadata['delta_strength'],
                'obi': metadata['obi'],
                'wall_detected': metadata['wall_detected'],
                'funding_rate': metadata['funding_rate'],
                'confidence': confidence
            })
        except Exception as e:
            logger.error(f"❌ BigQuery setup log failed: {e}")
        
        # KROK 2: Wyślij do bot_service
        BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"
        
        try:
            response = requests.post(BOT_SERVICE_URL, json=payload, timeout=5)
            if response.status_code == 200:
                logger.warning(f"🚀 SYGNAŁ WYSŁANY: {symbol} {direction} @ {entry_price:.2f} | Confidence: {confidence:.1f}/100")
            else:
                logger.error(f"❌ Bot Service Error: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"❌ Krytyczny błąd wysyłki sygnału: {e}")

    # ========================================
    # 5. WYKRYWANIE ZDARZEŃ (Liquidations, Walls)
    # ========================================
    
    def _check_liquidation_cascade(self, symbol):
        """Wykrywa kaskadę likwidacji i loguje do BigQuery"""
        recent_liqs = self.liquidations.get(symbol, [])
        
        if not recent_liqs:
            return
        
        total_volume = sum([e.value_usd for e in recent_liqs])
        
        if total_volume < self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            return
        
        # Określ dominującą stronę
        long_liqs = [e for e in recent_liqs if e.side == 'Buy']
        short_liqs = [e for e in recent_liqs if e.side == 'Sell']
        
        long_volume = sum([e.value_usd for e in long_liqs])
        short_volume = sum([e.value_usd for e in short_liqs])
        
        if long_volume > short_volume * 1.5:
            cascade_type = 'LONG_CASCADE'
            dominant_volume = long_volume
        elif short_volume > long_volume * 1.5:
            cascade_type = 'SHORT_CASCADE'
            dominant_volume = short_volume
        else:
            return  # Mixed
        
        # Loguj do BigQuery
        try:
            self.bq_logger.log_liquidation_cascade({
                'event_id': f"LIQ-{symbol}-{int(time.time())}",
                'symbol': symbol,
                'cascade_type': cascade_type,
                'total_volume_usd': total_volume,
                'dominant_volume_usd': dominant_volume,
                'count': len(recent_liqs)
            })
            logger.warning(f"🔥 LIQUIDATION CASCADE: {symbol} - {cascade_type} - ${total_volume:,.0f}")
        except Exception as e:
            logger.error(f"❌ BigQuery liq log failed: {e}")
    
    def _log_dom_walls(self, symbol, dom_snapshot):
        """Loguje znaczące ściany w DOM do BigQuery"""
        
        # Loguj tylko jeśli ściana jest blisko ceny (< 0.5%)
        mid_price = (dom_snapshot.best_bid + dom_snapshot.best_ask) / 2
        
        for wall in dom_snapshot.bid_walls:
            distance_pct = wall.distance_from_mid / mid_price * 100
            
            if distance_pct < 0.5:  # Blisko ceny
                try:
                    self.bq_logger.log_dom_wall({
                        'symbol': symbol,
                        'side': 'BID',
                        'price': wall.price,
                        'size': wall.size,
                        'obi': dom_snapshot.obi
                    })
                except Exception as e:
                    logger.error(f"❌ DOM wall log failed: {e}")
        
        for wall in dom_snapshot.ask_walls:
            distance_pct = wall.distance_from_mid / mid_price * 100
            
            if distance_pct < 0.5:
                try:
                    self.bq_logger.log_dom_wall({
                        'symbol': symbol,
                        'side': 'ASK',
                        'price': wall.price,
                        'size': wall.size,
                        'obi': dom_snapshot.obi
                    })
                except Exception as e:
                    logger.error(f"❌ DOM wall log failed: {e}")

    # ========================================
    # 6. DELTA DIVERGENCE DETECTION (POPRAWIONA IMPLEMENTACJA)
    # ========================================
    
    def _detect_delta_divergence(self, symbol) -> dict:
        """
        Wykrywa dywergencję Delta vs Price.
        
        BULLISH DIVERGENCE:
        - Cena robi niższy dołek (Lower Low)
        - Delta robi wyższy dołek (Higher Low) <- Mniej sprzedaży!
        
        BEARISH DIVERGENCE:
        - Cena robi wyższy szczyt (Higher High)
        - Delta robi niższy szczyt (Lower High) <- Mniej kupna!
        """
        hist = list(self.delta_history[symbol])
        
        if len(hist) < 30:
            return {'detected': False}
        
        # Ostatnie 30 wartości
        recent = hist[-30:]
        
        prices = [d['price'] for d in recent]
        deltas = [d['delta'] for d in recent]
        
        # Znajdź lokalne dołki w cenie
        price_lows = self._find_local_extrema(prices, find_lows=True, window=3)
        
        if len(price_lows) >= 2:
            # Ostatnie 2 dołki
            last_idx = price_lows[-1]
            prev_idx = price_lows[-2]
            
            last_price = prices[last_idx]
            prev_price = prices[prev_idx]
            
            last_delta = deltas[last_idx]
            prev_delta = deltas[prev_idx]
            
            # Bullish Divergence?
            if last_price < prev_price and last_delta > prev_delta:
                strength = abs(last_delta - prev_delta)
                return {
                    'detected': True,
                    'type': 'BULLISH',
                    'strength': strength
                }
        
        # Znajdź lokalne szczyty
        price_highs = self._find_local_extrema(prices, find_lows=False, window=3)
        
        if len(price_highs) >= 2:
            last_idx = price_highs[-1]
            prev_idx = price_highs[-2]
            
            last_price = prices[last_idx]
            prev_price = prices[prev_idx]
            
            last_delta = deltas[last_idx]
            prev_delta = deltas[prev_idx]
            
            # Bearish Divergence?
            if last_price > prev_price and last_delta < prev_delta:
                strength = abs(last_delta - prev_delta)
                return {
                    'detected': True,
                    'type': 'BEARISH',
                    'strength': strength
                }
        
        return {'detected': False}
    
    def _find_local_extrema(self, data, find_lows=True, window=3):
        """Znajduje indeksy lokalnych dołków lub szczytów"""
        extrema = []
        
        for i in range(window, len(data) - window):
            if find_lows:
                if data[i] == min(data[i-window:i+window+1]):
                    extrema.append(i)
            else:
                if data[i] == max(data[i-window:i+window+1]):
                    extrema.append(i)
        
        return extrema

    # ========================================
    # 7. NARZĘDZIA POMOCNICZE
    # ========================================
    
    def _calculate_delta_window(self, symbol: str, seconds: int) -> float:
        """Oblicza Delta dla ostatnich X sekund"""
        cutoff = int(time.time() * 1000) - (seconds * 1000)
        recent = [t for t in self.trades[symbol] if t['timestamp'] > cutoff]
        
        buy_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Buy'])
        sell_v = sum([t['qty'] * t['price'] for t in recent if t['side'] == 'Sell'])
        
        return buy_v - sell_v

    def _detect_walls(self, levels: List[Tuple[float, float]], side: str) -> List[OrderBookWall]:
        """Wykrywa ściany w Order Booku"""
        if not levels or len(levels) < 10:
            return []
        
        avg_size = sum([q for _, q in levels[:20]]) / min(20, len(levels))
        threshold = avg_size * self.DOM_WALL_MULTIPLIER
        
        walls = []
        for p, q in levels[:20]:
            if q > threshold:
                walls.append(OrderBookWall(
                    price=p,
                    size=q,
                    distance_from_mid=0.0,  # Będzie obliczone później
                    side=side
                ))
        
        return walls
    
    def get_full_context(self, symbol):
        """Endpoint dla /metrics API - zwraca pełny kontekst"""
        engine = self.engines.get(symbol)
        dom = self.orderbook_snapshots.get(symbol)
        ticker = self.tickers.get(symbol)
        
        return {
            'symbol': symbol,
            'structure': {
                'last_swing_high': engine.last_swing_high if engine else None,
                'last_swing_low': engine.last_swing_low if engine else None
            },
            'dom': {
                'obi': dom.obi if dom else None,
                'bid_walls': len(dom.bid_walls) if dom else 0,
                'ask_walls': len(dom.ask_walls) if dom else 0
            },
            'ticker': ticker
        }