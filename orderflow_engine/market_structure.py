import logging
from collections import deque

logger = logging.getLogger(__name__)

class MarketStructureEngine:
    def __init__(self, lookback_bars=500):
        self.candles = deque(maxlen=lookback_bars)
        self.last_swing_high = None
        self.last_swing_low = None
        self.major_high = None # Yearly/Daily High
        self.major_low = None  # Yearly/Daily Low

    def update_candles(self, open_p, high_p, low_p, close_p, ts):
        self.candles.append({'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'ts': ts})
        return self._analyze_structure()

    def _analyze_structure(self):
        if len(self.candles) < 10: return None
        highs = [c['high'] for c in self.candles]
        lows = [c['low'] for c in self.candles]
        i = len(highs) - 3
        if highs[i] == max(highs[i-2:i+3]): self.last_swing_high = highs[i]
        if lows[i] == min(lows[i-2:i+3]): self.last_swing_low = lows[i]
        return {'swing_high': self.last_swing_high, 'swing_low': self.last_swing_low}

    def is_sweep_happening(self, current_price):
        """Sprawdza czy cena aktualnie wybija ostatni płynny poziom."""
        if self.last_swing_low and current_price < self.last_swing_low:
            return True
        if self.last_swing_high and current_price > self.last_swing_high:
            return True
        return False