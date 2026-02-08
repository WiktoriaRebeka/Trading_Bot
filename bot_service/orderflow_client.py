# Lokalizacja: bot_service/orderflow_client.py
import asyncio
import logging
from typing import Dict, Any, Optional
import aiohttp
import time

logger = logging.getLogger(__name__)

class AsyncOrderFlowClient:
    """
    Asynchroniczny klient do OrderFlow Engine.
    - get_metrics_async(symbol) -> Dict[str, Any]
    - close() -> zamyka sesję
    """

    def __init__(self, base_url: str, timeout_s: float = 0.5, max_retries: int = 2, backoff: float = 0.1):
        self.base_url = base_url.rstrip('/')
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: Optional[aiohttp.ClientSession] = None
        self._max_retries = max_retries
        self._backoff = backoff

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def get_metrics_async(self, symbol: str) -> Dict[str, Any]:
        """
        Pobiera metryki dla symbolu z OrderFlow Engine.
        Zwraca dict lub pusty dict w przypadku błędu/timeout.
        """
        await self._ensure_session()
        url = f"{self.base_url}/metrics/{symbol}"
        attempt = 0
        while True:
            attempt += 1
            try:
                start = time.time()
                async with self._session.get(url) as resp:
                    elapsed = (time.time() - start) * 1000
                    if resp.status != 200:
                        logger.warning(f"[OrderFlow] non-200 ({resp.status}) for {symbol} (attempt {attempt})")
                        return {}
                    data = await resp.json()
                    logger.debug(f"[OrderFlow] metrics fetched for {symbol} in {int(elapsed)}ms")
                    return data if isinstance(data, dict) else {}
            except asyncio.TimeoutError:
                logger.warning(f"[OrderFlow] timeout for {symbol} (attempt {attempt})")
            except Exception as e:
                logger.exception(f"[OrderFlow] error fetching metrics for {symbol} (attempt {attempt}): {e}")
            if attempt >= self._max_retries:
                logger.warning(f"[OrderFlow] giving up fetching metrics for {symbol} after {attempt} attempts")
                return {}
            await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class SyncOrderFlowAdapter:
    """
    Synchronous adapter for AsyncOrderFlowClient.
    Use in synchronous code (Flask) to fetch metrics with a short blocking wait.
    It runs the async call in a dedicated event loop per process or uses the running loop if available.
    """

    def __init__(self, async_client: AsyncOrderFlowClient, loop: Optional[asyncio.AbstractEventLoop] = None, sync_timeout: float = 0.6):
        self._client = async_client
        self._loop = loop or asyncio.new_event_loop()
        # If we created a new loop, run it in a background thread
        self._own_loop = loop is None
        self._sync_timeout = sync_timeout
        if self._own_loop:
            import threading
            self._thread = threading.Thread(target=self._run_loop_forever, daemon=True)
            self._thread.start()

    def _run_loop_forever(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def get_metrics(self, symbol: str) -> Dict[str, Any]:
        """
        Blocking call that returns metrics dict or {} on failure/timeout.
        """
        coro = self._client.get_metrics_async(symbol)
        try:
            # If loop is running in this thread, use run_until_complete carefully
            if self._loop.is_running():
                # schedule and wait with timeout
                fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
                return fut.result(timeout=self._sync_timeout)
            else:
                return self._loop.run_until_complete(asyncio.wait_for(coro, timeout=self._sync_timeout))
        except Exception as e:
            logger.warning(f"[OrderFlowAdapter] get_metrics failed for {symbol}: {e}")
            return {}

    def close(self):
        try:
            if self._own_loop:
                # stop loop and close
                def _stop():
                    self._loop.stop()
                self._loop.call_soon_threadsafe(_stop)
                self._thread.join(timeout=1.0)
            # schedule async client close
            try:
                if self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._client.close(), self._loop).result(timeout=1.0)
                else:
                    self._loop.run_until_complete(self._client.close())
            except Exception:
                pass
        except Exception:
            logger.exception("[OrderFlowAdapter] error during close")