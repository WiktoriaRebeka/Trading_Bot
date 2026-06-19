# orderflow_engine/market_structure.py
import logging
from collections import deque
import numpy as np

logger = logging.getLogger(__name__)

class MarketStructureEngine:
    def __init__(self, lookback_bars: int = 500):
        self.lookback = lookback_bars
        self.candles = deque(maxlen=lookback_bars)
        self.last_swing_high = None
        self.last_swing_low = None
        self.major_high = None  # Yearly/Daily High
        self.major_low = None   # Yearly/Daily Low

    def update_candles(self, open_p, high_p, low_p, close_p, ts):
        """Aktualizuje historię świec i przelicza strukturę."""
        self.candles.append({
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'ts': ts
        })
        self._analyze_structure()

    def _analyze_structure(self):
        """Detekcja swingów metodą fraktalną."""
        if len(self.candles) < 10:
            return

        c = list(self.candles)
        
        # Swing High (świeca i-2 jest najwyższa w oknie 5 świec)
        if c[-3]['high'] == max(x['high'] for x in c[-5:]):
            self.last_swing_high = c[-3]['high']
            
        # Swing Low (świeca i-2 jest najniższa w oknie 5 świec)
        if c[-3]['low'] == min(x['low'] for x in c[-5:]):
            self.last_swing_low = c[-3]['low']

    def is_sweep_happening(self, current_price: float) -> bool:
        """Sprawdza czy cena wybija ostatni swing."""
        if self.last_swing_high is None or self.last_swing_low is None:
            return False
            
        if current_price > self.last_swing_high:
            return True
        if current_price < self.last_swing_low:
            return True
            
        return False

    def get_swing_strength(self, direction: str = "LONG") -> int:
        """
        Oblicza siłę struktury przy użyciu numpy (multi-touch count).
        LONG: dotknięcia last_swing_low; SHORT: dotknięcia last_swing_high.
        """
        if not self.candles:
            return 1

        side = str(direction).upper()
        if side == "SHORT":
            if self.last_swing_high is None:
                return 1
            prices = np.array([c['high'] for c in self.candles])
            ref = self.last_swing_high
        else:
            if self.last_swing_low is None:
                return 1
            prices = np.array([c['low'] for c in self.candles])
            ref = self.last_swing_low

        diffs = np.abs(prices - ref) / ref
        touches = np.sum(diffs < 0.0015)
        return int(min(touches, 5))

    def get_latest_swing(self):
        return {
            "high": self.last_swing_high,
            "low": self.last_swing_low,
        }

    def get_sweep_direction(self, current_price: float) -> str:
        if self.last_swing_low is not None and current_price < self.last_swing_low:
            return "LONG"
        if self.last_swing_high is not None and current_price > self.last_swing_high:
            return "SHORT"
        return "NONE"