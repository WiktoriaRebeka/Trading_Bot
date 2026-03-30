# orderflow_engine/backfiller.py
import os
import aiohttp
import logging
from typing import List, NamedTuple, Any, Optional

logger = logging.getLogger(__name__)


class FetchHistoryResult(NamedTuple):
    """Jawny status backfillu: rows + ok (True tylko przy sukcesie z niepustymi świecami)."""
    rows: List[dict[str, Any]]
    ok: bool


class HistoryBackfiller:
    def __init__(self, base_url="https://api.bybit.com"):
        self.base_url = base_url
        self.endpoint = f"{base_url}/v5/market/kline"
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        total = float(os.environ.get("BACKFILL_HTTP_TIMEOUT_SEC", "90"))
        total = max(30.0, total)
        timeout = aiohttp.ClientTimeout(total=total)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.close()
            self._session = None
        return False

    async def fetch_history(self, symbol, interval='1', limit=1000) -> FetchHistoryResult:
        """Pobiera klines; ok=True wyłącznie gdy otrzymano niepustą listę świec."""
        if self._session is None:
            raise RuntimeError("HistoryBackfiller wymaga async with przed fetch_history")

        params = {
            "category": "linear",
            "symbol": symbol.replace('.P', ''),
            "interval": interval,
            "limit": limit
        }
        try:
            async with self._session.get(self.endpoint, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("retCode") == 0 and data.get("result"):
                        klines = data["result"]["list"][::-1]
                        if not klines:
                            logger.warning(f"⚠️ Backfill {symbol}: retCode=0 ale pusta lista klines")
                            return FetchHistoryResult([], False)
                        rows = [{
                            'ts': int(k[0]),
                            'open': float(k[1]),
                            'high': float(k[2]),
                            'low': float(k[3]),
                            'close': float(k[4]),
                            'volume': float(k[5])
                        } for k in klines]
                        return FetchHistoryResult(rows, True)
                    else:
                        logger.error(
                            f"❌ Backfill {symbol}: retCode={data.get('retCode')} "
                            f"retMsg={data.get('retMsg')} result={bool(data.get('result'))}"
                        )
                else:
                    logger.error(f"❌ Bybit API Error {symbol}: HTTP {response.status}")
        except TimeoutError:
            logger.warning(
                "⚠️ Backfill %s interval=%s: HTTP timeout (limit całkowity aiohttp) — kolejna próba może pomóc",
                symbol,
                interval,
            )
        except aiohttp.ClientError as e:
            logger.warning(
                "⚠️ Backfill %s interval=%s: błąd klienta HTTP %s: %s",
                symbol,
                interval,
                type(e).__name__,
                e,
            )
        except Exception as e:
            logger.error(f"❌ Błąd backfillu dla {symbol}: {type(e).__name__}: {e}", exc_info=True)
        return FetchHistoryResult([], False)
