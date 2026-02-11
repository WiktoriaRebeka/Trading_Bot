import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class CandleBuilder:
    def __init__(self, symbol, interval_minutes=1):
        self.symbol = symbol
        self.interval = interval_minutes * 60 * 1000  # ms
        self.current_candle = None

    def process_tick(self, price, qty, ts_ms):

        candle_start_ms = (ts_ms // self.interval) * self.interval
        completed_candle = None

        if self.current_candle is None:
            self._start_new_candle(candle_start_ms, price)
            self.current_candle['volume'] += qty
    
        elif candle_start_ms > self.current_candle['ts']:
            completed_candle = self.current_candle
            self._start_new_candle(candle_start_ms, price)
            self.current_candle['volume'] += qty
        else:
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