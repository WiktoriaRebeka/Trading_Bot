# simulate_signals.py
import asyncio
import random
from datetime import datetime, timedelta

from .signal_detector import SwingPoint, LiquidationEvent, DeltaPoint, DomSnapshot
from . import market_structure, metrics_processor

# Mockowanie funkcji w metrics_processor i market_structure jeśli nie masz jeszcze implementacji
# (tylko do lokalnego testu — usuń w produkcji)

def mock_setup(symbol):
    # przykładowe dane
    market_structure._mock_swing = SwingPoint(price=50000.0, timestamp=datetime.utcnow() - timedelta(hours=2))
    market_structure._mock_direction = "LONG"
    metrics_processor._mock_dom = {"bids":[(49950.0, 1200)], "asks":[(50010.0, 200)], "obi": 0.6}
    metrics_processor._mock_liqs = [{"side":"LONG","volume_usd":60000.0,"timestamp":datetime.utcnow()}]
    metrics_processor._mock_deltas = [{"price":49980.0,"delta":-1200.0,"timestamp":datetime.utcnow()-timedelta(seconds=5)},
                                     {"price":49960.0,"delta":-800.0,"timestamp":datetime.utcnow()}]
    metrics_processor._mock_funding = 0.0005

async def run_sim():
    symbol = "BTCUSDT"
    mock_setup(symbol)
    for i in range(3):
        price = 49950.0 + random.uniform(-10, 20)
        tick_size = 0.5
        funding = metrics_processor._mock_funding
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(run_sim())