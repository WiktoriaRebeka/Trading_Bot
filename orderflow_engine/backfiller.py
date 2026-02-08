# orderflow_engine/backfiller.py
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class HistoryBackfiller:
    def __init__(self, base_url="https://api.bybit.com"):
        self.endpoint = f"{base_url}/v5/market/kline"

    def fetch_history(self, symbol, interval='1', limit=1000):
        params = {
            "category": "linear",
            "symbol": symbol.replace('.P', ''),
            "interval": interval,
            "limit": limit
        }
        try:
            response = requests.get(self.endpoint, params=params, timeout=10)
            data = response.json()
            if data.get("retCode") == 0 and data.get("result"):
                klines = data["result"]["list"][::-1]
                return [{
                    'ts': int(k[0]),
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5])
                } for k in klines]
        except Exception as e:
            logger.error(f"❌ Błąd backfillu dla {symbol}: {e}")
        return []