# orderflow_engine/metrics_processor.py
import logging
from collections import deque, namedtuple
from datetime import datetime, timezone
from typing import Dict, Any, Deque, Optional, List

logger = logging.getLogger(__name__)

# --- Stałe Obliczeniowe ---
M2_DELTA_WINDOW_SECONDS = 120
EMA_RS_PERIOD = 5

# Struktura dla trade'ów w buforze
TradeRecord = namedtuple('TradeRecord', ['ts_ms', 'side_sign', 'qty'])

class MetricsState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.trades_buffer: Deque[TradeRecord] = deque()
        self.m2_delta: float = 0.0
        self.last_price: float = 0.0  # Mark Price
        self.last_rs: float = 0.0
        self.m5_rs_ratio: float = 0.0
        self.ema_history: Deque[float] = deque()
        self.last_update_ts: Optional[datetime] = None

    def update_ema_rs(self, new_rs: float):
        self.last_rs = new_rs
        self.last_update_ts = datetime.now(timezone.utc)
        
        if not self.ema_history:
            self.ema_history.append(new_rs)
            self.m5_rs_ratio = new_rs
            return

        if len(self.ema_history) < EMA_RS_PERIOD:
            self.ema_history.append(new_rs)
            self.m5_rs_ratio = sum(self.ema_history) / len(self.ema_history)
        else:
            # EMA: alpha = 2 / (N + 1)
            alpha = 2 / (EMA_RS_PERIOD + 1)
            ema_old = self.m5_rs_ratio
            self.m5_rs_ratio = alpha * new_rs + (1 - alpha) * ema_old
            self.ema_history.popleft()
            self.ema_history.append(new_rs)

class OrderFlowMetrics:
    def __init__(self, symbols: List[str], benchmark: str):
        self.states: Dict[str, MetricsState] = {s: MetricsState(s) for s in symbols}
        self.benchmark_symbol = benchmark
        self.last_price_btc: float = 0.0
        
        if benchmark not in self.states:
            self.states[benchmark] = MetricsState(benchmark)

    def process_trade(self, ts_ms: int, symbol_raw: str, side: str, qty: float):
        if symbol_raw not in self.states:
            return

        metrics = self.states[symbol_raw]
        
        side_sign = 1 if side == "Buy" else -1
        metrics.trades_buffer.append(TradeRecord(ts_ms, side_sign, qty))
        
        self._calculate_m2_delta(metrics)

    def _calculate_m2_delta(self, metrics: MetricsState):
        current_ts_ms = metrics.trades_buffer[-1].ts_ms 
        
        while metrics.trades_buffer and (current_ts_ms - metrics.trades_buffer[0].ts_ms > M2_DELTA_WINDOW_SECONDS * 1000):
            metrics.trades_buffer.popleft()
        
        delta = sum(record.side_sign * record.qty for record in metrics.trades_buffer)
        metrics.m2_delta = delta

    def process_ticker(self, symbol_raw: str, mark_price: float):
        if mark_price <= 0.0:
            return

        if symbol_raw == self.benchmark_symbol:
            self.last_price_btc = mark_price
            self._recalculate_all_rs()
            return

        if symbol_raw in self.states:
            metrics = self.states[symbol_raw]
            metrics.last_price = mark_price
            self._recalculate_rs_for_symbol(metrics)

    def _recalculate_rs_for_symbol(self, metrics: MetricsState):
        if self.last_price_btc > 0 and metrics.last_price > 0:
            rs = metrics.last_price / self.last_price_btc
            metrics.update_ema_rs(rs)

    def _recalculate_all_rs(self):
        if self.last_price_btc > 0:
            for metrics in self.states.values():
                if metrics.symbol != self.benchmark_symbol:
                    self._recalculate_rs_for_symbol(metrics)

    def get_metrics_json(self, symbol: str) -> Dict[str, Any]:
        metrics = self.states.get(symbol)
        
        if not metrics or metrics.last_update_ts is None:
            return {
                "symbol": symbol,
                "m2_delta": 0.0,
                "m5_rs_ratio": 0.0,
                "as_of": datetime.now(timezone.utc).isoformat()
            }
        
        return {
            "symbol": metrics.symbol,
            "m2_delta": metrics.m2_delta,
            "m5_rs_ratio": metrics.m5_rs_ratio,
            "as_of": metrics.last_update_ts.isoformat()
        }