# orderflow_engine/market_structure.py

import logging
from collections import deque

logger = logging.getLogger(__name__)


class MarketStructureEngine:
    def __init__(self, lookback_bars: int = 500):
        self.candles = deque(maxlen=lookback_bars)
        self.last_swing_high = None
        self.last_swing_low = None
        self.major_high = None  # Yearly/Daily High
        self.major_low = None   # Yearly/Daily Low

    def update_candles(self, open_p, high_p, low_p, close_p, ts):
        """
        Aktualizuje bufor świec i przelicza strukturę rynku.
        """
        self.candles.append(
            {
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': close_p,
                'ts': ts,
            }
        )
        return self._analyze_structure()

    def _analyze_structure(self):
        """
        Prosta detekcja swing high / swing low:
        - swing high: lokalne maksimum w oknie 5 świec
        - swing low: lokalne minimum w oknie 5 świec
        """
        if len(self.candles) < 10:
            return None

        highs = [c['high'] for c in self.candles]
        lows = [c['low'] for c in self.candles]

        i = len(highs) - 3  # środkowa świeca z okna [i-2, i+2]

        if highs[i] == max(highs[i - 2:i + 3]):
            self.last_swing_high = highs[i]
        if lows[i] == min(lows[i - 2:i + 3]):
            self.last_swing_low = lows[i]

        return {
            'swing_high': self.last_swing_high,
            'swing_low': self.last_swing_low,
        }

    def is_sweep_happening(self, current_price: float) -> bool:
        """
        Sprawdza, czy cena aktualnie wybija ostatni płynny poziom (swing high/low).
        """
        if self.last_swing_low is not None and current_price < self.last_swing_low:
            return True
        if self.last_swing_high is not None and current_price > self.last_swing_high:
            return True
        return False

    def get_swing_strength(self) -> int:
        """
        Zwraca 'siłę' swing point (ile razy ten poziom był testowany).

        Returns:
            1 = Single swing
            2 = Double bottom/top
            3+ = Triple+ (Equal Lows/Highs)
        """
        if self.last_swing_low is None:
            return 1

        tolerance = 0.0015  # 0.15%
        touches = 0

        for candle in self.candles:
            if abs(candle['low'] - self.last_swing_low) / self.last_swing_low < tolerance:
                touches += 1

        return min(touches, 5)  # Cap at 5

    # ============================================================
    # === PUBLIC HELPERS FOR INTEGRATION / SIGNAL CONTEXT
    # ============================================================

    def get_latest_swing(self):
        """
        Zwraca ostatni swing w prostym formacie:
        {
            "high": float | None,
            "low": float | None
        }
        """
        return {
            "high": self.last_swing_high,
            "low": self.last_swing_low,
        }

    def get_sweep_direction(self, current_price: float) -> str:
        """
        Zwraca kierunek sweepa względem ostatnich swingów:
        - 'LONG'  -> cena wybija ostatni swing low (stop hunt longów)
        - 'SHORT' -> cena wybija ostatni swing high (stop hunt shortów)
        - 'NONE'  -> brak sweepa
        """
        if self.last_swing_low is not None and current_price < self.last_swing_low:
            return "LONG"
        if self.last_swing_high is not None and current_price > self.last_swing_high:
            return "SHORT"
        return "NONE"