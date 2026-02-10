# orderflow_engine/backfiller.py
import aiohttp
import logging
import asyncio

logger = logging.getLogger(__name__)

class HistoryBackfiller:
    def __init__(self, base_url="https://api.bybit.com"):
        self.base_url = base_url
        self.endpoint = f"{base_url}/v5/market/kline"

    async def fetch_history(self, symbol, interval='1', limit=1000):
        """Wersja ASYNCHRONICZNA - nie blokuje bota."""
        params = {
            "category": "linear",
            "symbol": symbol.replace('.P', ''),
            "interval": interval,
            "limit": limit
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.endpoint, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
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
                    else:
                        logger.error(f"❌ Bybit API Error {symbol}: {response.status}")
        except Exception as e:
            logger.error(f"❌ Błąd backfillu dla {symbol}: {e}")
        return []