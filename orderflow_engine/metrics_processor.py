# orderflow_engine/metrics_processor.py
# WERSJA: 5.0 - Institutional Footprint Detection
# Nowe funkcje: Liquidation Cascade, DOM Walls, OBI

import time
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# ========================================
# NOWE MODELE DANYCH
# ========================================

@dataclass
class LiquidationEvent:
    """Pojedyncze zdarzenie likwidacji"""
    symbol: str
    side: str          # 'Buy' = Long liquidation, 'Sell' = Short liquidation
    price: float
    qty: float
    time: int          # Unix timestamp (ms)
    value_usd: float   # qty * price

@dataclass
class OrderBookWall:
    """Ściana w Order Booku"""
    price: float
    size: float
    distance_from_mid: float  # W tickach
    side: str  # 'bid' or 'ask'

@dataclass
class DOMSnapshot:
    """Snapshot Order Booka"""
    symbol: str
    bids: List[Tuple[float, float]]  # [(price, qty), ...]
    asks: List[Tuple[float, float]]
    timestamp: int
    obi: float  # Order Book Imbalance
    bid_walls: List[OrderBookWall]
    ask_walls: List[OrderBookWall]


class OrderFlowMetrics:
    """
    Główny procesor metryk Order Flow.
    
    NOWE MOŻLIWOŚCI (V5.0):
    - Liquidation Cascade Detection
    - DOM Wall Detection
    - Order Book Imbalance (OBI)
    - Absorption Detection (Delta Divergence)
    """
    
    def __init__(self, firestore_client=None):
        self.firestore = firestore_client
        
        # ========================================
        # EXISTING BUFFERS (nie zmieniamy)
        # ========================================
        self.trades = defaultdict(lambda: deque(maxlen=1000))  # Last 1000 trades per symbol
        self.tickers = {}  # Latest ticker per symbol
        
        # ========================================
        # NEW BUFFERS (V5.0)
        # ========================================
        self.liquidations = defaultdict(list)  # Rolling 60s window per symbol
        self.orderbook_snapshots = {}          # Latest DOM per symbol
        self.delta_history = defaultdict(lambda: deque(maxlen=100))  # For divergence detection
        
        # Konfiguracja
        self.LIQUIDATION_WINDOW_SECONDS = 60
        self.LIQUIDATION_CASCADE_THRESHOLD_USD = 50000  # 50k USD
        self.DOM_WALL_MULTIPLIER = 3.0  # Ściana = 3x avg size
        
        logger.info("✅ OrderFlowMetrics V5.0 zainicjowany (Liquidations + DOM + Delta)")
    
    # ========================================
    # EXISTING METHODS (bez zmian)
    # ========================================
    
    def process_trade(self, timestamp: int, symbol: str, side: str, qty: float, price: float):
        """
        Przetwarza pojedynczy trade (dla Delta).
        ISTNIEJĄCA LOGIKA - bez zmian.
        """
        self.trades[symbol].append({
            'timestamp': timestamp,
            'side': side,
            'qty': qty,
            'price': price
        })
        
        # Oblicz Delta dla ostatniej minuty
        delta = self._calculate_delta_1m(symbol)
        
        # Zapisz do historii (dla divergence detection)
        self.delta_history[symbol].append({
            'timestamp': timestamp,
            'delta': delta,
            'price': price
        })
    
    def process_ticker(self, symbol: str, price: float, funding_rate: float, 
                      open_interest: float, volume_24h: float):
        """
        Przetwarza ticker (cena, FR, OI).
        ISTNIEJĄCA LOGIKA - bez zmian.
        """
        self.tickers[symbol] = {
            'price': price,
            'funding_rate': funding_rate,
            'open_interest': open_interest,
            'volume_24h': volume_24h,
            'timestamp': int(time.time() * 1000)
        }
    
    def _calculate_delta_1m(self, symbol: str) -> float:
        """
        Oblicza Delta (Buy Volume - Sell Volume) dla ostatniej minuty.
        ISTNIEJĄCA LOGIKA.
        """
        cutoff = int(time.time() * 1000) - 60000  # 60 sekund
        recent_trades = [t for t in self.trades[symbol] if t['timestamp'] > cutoff]
        
        buy_volume = sum([t['qty'] * t['price'] for t in recent_trades if t['side'] == 'Buy'])
        sell_volume = sum([t['qty'] * t['price'] for t in recent_trades if t['side'] == 'Sell'])
        
        return buy_volume - sell_volume
    
    # ========================================
    # NEW METHODS (V5.0) - LIQUIDATIONS
    # ========================================
    
    def process_liquidation(self, liq_data: dict):
        """
        Przetwarza event likwidacji.
        
        NOWA LOGIKA:
        1. Zapisuje event do bufora (60s rolling window)
        2. Sprawdza czy wystąpiła kaskada likwidacji
        3. Emituje event jeśli threshold przekroczony
        """
        symbol = liq_data.get('symbol')
        
        if not symbol:
            return
        
        # Utwórz event
        liq_event = LiquidationEvent(
            symbol=symbol,
            side=liq_data['side'],
            price=liq_data['price'],
            qty=liq_data['qty'],
            time=liq_data['time'],
            value_usd=liq_data['qty'] * liq_data['price']
        )
        
        # Dodaj do bufora
        self.liquidations[symbol].append(liq_event)
        
        # Usuń stare eventy (poza oknem 60s)
        cutoff_time = int(time.time() * 1000) - (self.LIQUIDATION_WINDOW_SECONDS * 1000)
        self.liquidations[symbol] = [
            e for e in self.liquidations[symbol]
            if e.time > cutoff_time
        ]
        
        # Sprawdź czy jest kaskada
        self._check_liquidation_cascade(symbol)
    
    def _check_liquidation_cascade(self, symbol: str):
        """
        Wykrywa kaskadę likwidacji (Stop Hunt).
        
        WARUNEK:
        - W ciągu ostatnich 60s zlikwidowano > threshold USD
        - Dominacja jednej strony (Long lub Short)
        """
        recent_liqs = self.liquidations[symbol]
        
        if not recent_liqs:
            return
        
        # Suma wartości w USD
        total_volume_usd = sum([e.value_usd for e in recent_liqs])
        
        # Sprawdź threshold
        if total_volume_usd < self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            return
        
        # Określ dominującą stronę
        long_liqs = [e for e in recent_liqs if e.side == 'Buy']   # Long liquidations
        short_liqs = [e for e in recent_liqs if e.side == 'Sell']  # Short liquidations
        
        long_volume = sum([e.value_usd for e in long_liqs])
        short_volume = sum([e.value_usd for e in short_liqs])
        
        if long_volume > short_volume * 2:
            cascade_type = 'LONG_CASCADE'  # Setup na Long (Longs were hunted)
            dominant_volume = long_volume
        elif short_volume > long_volume * 2:
            cascade_type = 'SHORT_CASCADE'  # Setup na Short
            dominant_volume = short_volume
        else:
            return  # Mixed, nie clear signal
        
        # EMIT EVENT
        event_data = {
            'type': 'LIQUIDATION_CASCADE',
            'symbol': symbol,
            'cascade_type': cascade_type,
            'total_volume_usd': total_volume_usd,
            'dominant_volume_usd': dominant_volume,
            'count': len(recent_liqs),
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.warning(f"🔥 LIQUIDATION CASCADE: {symbol} - {cascade_type} - ${total_volume_usd:,.0f}")
        
        # Zapisz do Firestore
        if self.firestore:
            try:
                self.firestore.collection('liquidation_events').add(event_data)
            except Exception as e:
                logger.error(f"Błąd zapisu liquidation event: {e}")
    
    # ========================================
    # NEW METHODS (V5.0) - DOM / ORDER BOOK
    # ========================================
    
    def process_orderbook(self, ob_data: dict):
        """
        Przetwarza snapshot Order Booka (DOM L50).
        
        NOWA LOGIKA:
        1. Oblicza Order Book Imbalance (OBI)
        2. Wykrywa ściany (Liquidity Walls)
        3. Zapisuje snapshot
        4. Emituje eventy jeśli wykryto anomalie
        """
        symbol = ob_data['symbol']
        bids = ob_data['bids']
        asks = ob_data['asks']
        
        if not bids or not asks:
            return
        
        # 1. Oblicz OBI (Order Book Imbalance)
        obi = self._calculate_obi(bids, asks, depth=10)
        
        # 2. Wykryj ściany
        bid_walls = self._detect_walls(bids, side='bid')
        ask_walls = self._detect_walls(asks, side='ask')
        
        # 3. Utwórz snapshot
        mid_price = (bids[0][0] + asks[0][0]) / 2
        
        for wall in bid_walls:
            wall.distance_from_mid = mid_price - wall.price
        
        for wall in ask_walls:
            wall.distance_from_mid = wall.price - mid_price
        
        snapshot = DOMSnapshot(
            symbol=symbol,
            bids=bids[:20],  # Top 20 levels (zamiast 50, oszczędność pamięci)
            asks=asks[:20],
            timestamp=ob_data['timestamp'],
            obi=obi,
            bid_walls=bid_walls,
            ask_walls=ask_walls
        )
        
        # 4. Zapisz snapshot
        self.orderbook_snapshots[symbol] = snapshot
        
        # 5. Emit events jeśli wykryto ważne ściany
        if bid_walls:
            strongest_bid_wall = max(bid_walls, key=lambda w: w.size)
            
            # Event tylko jeśli ściana jest blisko ceny (< 0.5%)
            if strongest_bid_wall.distance_from_mid / mid_price < 0.005:
                self._emit_wall_event(symbol, strongest_bid_wall, obi)
        
        if ask_walls:
            strongest_ask_wall = max(ask_walls, key=lambda w: w.size)
            
            if strongest_ask_wall.distance_from_mid / mid_price < 0.005:
                self._emit_wall_event(symbol, strongest_ask_wall, obi)
    
    def _calculate_obi(self, bids: List[Tuple[float, float]], 
                       asks: List[Tuple[float, float]], depth: int = 10) -> float:
        """
        Oblicza Order Book Imbalance (OBI).
        
        OBI = (Bid Volume - Ask Volume) / (Bid Volume + Ask Volume)
        
        OBI > 0.3 → Bullish (przewaga kupna)
        OBI < -0.3 → Bearish (przewaga sprzedaży)
        """
        bid_volume = sum([price * qty for price, qty in bids[:depth]])
        ask_volume = sum([price * qty for price, qty in asks[:depth]])
        
        total_volume = bid_volume + ask_volume
        
        if total_volume == 0:
            return 0.0
        
        obi = (bid_volume - ask_volume) / total_volume
        
        return obi
    
    def _detect_walls(self, levels: List[Tuple[float, float]], side: str) -> List[OrderBookWall]:
        """
        Wykrywa ściany w Order Booku.
        
        DEFINICJA ŚCIANY:
        Zlecenie (lub klaster zleceń) który jest > threshold × średnia wielkość.
        """
        if not levels or len(levels) < 10:
            return []
        
        # Oblicz średnią wielkość zlecenia (top 20 levels)
        avg_size = sum([qty for _, qty in levels[:20]]) / min(20, len(levels))
        
        # Threshold
        wall_threshold = avg_size * self.DOM_WALL_MULTIPLIER
        
        # Znajdź ściany
        walls = []
        
        for price, qty in levels[:30]:  # Sprawdzaj tylko top 30 levels
            if qty > wall_threshold:
                wall = OrderBookWall(
                    price=price,
                    size=qty,
                    distance_from_mid=0.0,  # Będzie obliczone potem
                    side=side
                )
                walls.append(wall)
        
        return walls
    
    def _emit_wall_event(self, symbol: str, wall: OrderBookWall, obi: float):
        """
        Emituje event o wykryciu ściany w DOM.
        
        ZAPISUJE TYLKO WAŻNE EVENTY (nie każdy update).
        """
        event_data = {
            'type': 'DOM_WALL_DETECTED',
            'symbol': symbol,
            'side': wall.side.upper(),  # 'BID' or 'ASK'
            'price': wall.price,
            'size': wall.size,
            'distance_from_mid_pct': (wall.distance_from_mid / wall.price) * 100,
            'obi': obi,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.info(f"🛡️ DOM WALL: {symbol} - {wall.side.upper()} wall at ${wall.price:.2f} ({wall.size:.2f} units)")
        
        # Zapisz do Firestore
        if self.firestore:
            try:
                self.firestore.collection('dom_events').add(event_data)
            except Exception as e:
                logger.error(f"Błąd zapisu DOM event: {e}")
    
    # ========================================
    # HELPER METHODS - Data Retrieval
    # ========================================
    
    def get_latest_liquidation_data(self, symbol: str) -> dict:
        """
        Zwraca aktualne dane o likwidacjach dla symbolu.
        """
        recent_liqs = self.liquidations.get(symbol, [])
        
        if not recent_liqs:
            return {'detected': False}
        
        total_volume = sum([e.value_usd for e in recent_liqs])
        
        long_liqs = [e for e in recent_liqs if e.side == 'Buy']
        short_liqs = [e for e in recent_liqs if e.side == 'Sell']
        
        return {
            'detected': True,
            'total_volume_usd': total_volume,
            'count': len(recent_liqs),
            'long_liquidations': len(long_liqs),
            'short_liquidations': len(short_liqs)
        }
    
    def get_latest_dom_data(self, symbol: str) -> Optional[DOMSnapshot]:
        """
        Zwraca najnowszy snapshot DOM dla symbolu.
        """
        return self.orderbook_snapshots.get(symbol)
    
    def get_delta_divergence(self, symbol: str) -> dict:
        """
        Sprawdza czy występuje dywergencja Delta vs Price.
        
        BULLISH DIVERGENCE:
        - Cena robi niższy dołek
        - Delta robi wyższy dołek (mniej agresywnej sprzedaży)
        """
        delta_hist = list(self.delta_history.get(symbol, []))
        
        if len(delta_hist) < 20:
            return {'detected': False}
        
        # Pobierz ostatnie 20 wartości
        recent = delta_hist[-20:]
        
        # Znajdź lokalne dołki w cenie
        price_values = [d['price'] for d in recent]
        delta_values = [d['delta'] for d in recent]
        
        # Uproszczona detekcja (można ulepszyć)
        last_price = price_values[-1]
        prev_low_price = min(price_values[-10:-1])
        
        last_delta = delta_values[-1]
        prev_low_delta = min(delta_values[-10:-1])
        
        # Bullish divergence
        if last_price < prev_low_price and last_delta > prev_low_delta:
            return {
                'detected': True,
                'type': 'BULLISH',
                'strength': abs(last_delta - prev_low_delta)
            }
        
        # Bearish divergence
        elif last_price > max(price_values[-10:-1]) and last_delta < max(delta_values[-10:-1]):
            return {
                'detected': True,
                'type': 'BEARISH',
                'strength': abs(last_delta - max(delta_values[-10:-1]))
            }
        
        return {'detected': False}
    
    def get_full_context(self, symbol: str) -> dict:
        """
        To jest funkcja, którą wywoła Twój bot w momencie sygnału.
        Łączy Strukturę, OrderFlow i Płynność.
        """
        ticker = self.tickers.get(symbol, {})
        dom = self.orderbook_snapshots.get(symbol)
        liqs = self.get_latest_liquidation_data(symbol)
        div = self.get_delta_divergence(symbol)
        
        return {
            "price": ticker.get('price'),
            "funding_rate": ticker.get('funding_rate'),
            "open_interest": ticker.get('open_interest'),
            "obi": dom.obi if dom else 0,
            "liquidation_vol_60s": liqs.get('total_volume_usd', 0),
            "delta_divergence": div.get('detected', False),
            "delta_strength": div.get('strength', 0)
        }