import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class CandleBuilder:
    def __init__(self, symbol, interval_minutes=1):
        self.symbol = symbol
        self.interval = interval_minutes * 60 * 1000  # ms
        self.current_candle = None

    def process_tick(self, price, qty, ts_ms):
        """
        Główna logika lepienia świecy.
        Zwraca kompletną świecę (dict), jeśli właśnie zamknęła się minuta.
        """
        # Oblicz start minuty dla tego ticka (np. 10:05:42 -> 10:05:00)
        candle_start_ms = (ts_ms // self.interval) * self.interval
        
        completed_candle = None

        if self.current_candle is None:
            # Pierwszy tick w historii bota
            self._start_new_candle(candle_start_ms, price)
        
        elif candle_start_ms > self.current_candle['ts']:
            # Tick należy już do nowej minuty -> Zamykamy starą świecę
            completed_candle = self.current_candle
            self._start_new_candle(candle_start_ms, price)
        
        else:
            # Kontynuujemy aktualną świecę
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] += qty

        return completed_candle

    def _start_new_candle(self, ts, price):
        self.current_candle = {
            'ts': ts,
            'open': price,
            'high': price,
            'low': price,
            'close': price,
            'volume': 0
        }