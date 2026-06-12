# orderflow_engine/bot_sender.py
# WERSJA 1.1 - Asynchroniczna wysyłka alertów do bot_service (sesja współdzielona, retry, timeouty)

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

import aiohttp

BOT_SERVICE_URL = os.environ.get(
    "BOT_SERVICE_URL",
    "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts",
)

logger = logging.getLogger(__name__)
logger.info(
    f"[bot_sender] BOT_SERVICE_URL={'env' if os.environ.get('BOT_SERVICE_URL') else 'fallback'}: {BOT_SERVICE_URL}"
)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


def _client_timeout() -> aiohttp.ClientTimeout:
    total = float(os.environ.get("BOT_SERVICE_TIMEOUT_SEC", "120"))
    connect = float(os.environ.get("BOT_SERVICE_CONNECT_TIMEOUT_SEC", "20"))
    return aiohttp.ClientTimeout(total=total, connect=connect, sock_connect=connect)


async def _get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            connector = aiohttp.TCPConnector(
                limit=32,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                force_close=True,
            )
            _session = aiohttp.ClientSession(connector=connector)
        return _session


async def _close_session_safely() -> None:
    global _session
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
        _session = None


async def _reset_session() -> None:
    """Zamknij i wyczyść współdzieloną sesję — kolejne _get_session() utworzy nową."""
    await _close_session_safely()


async def send_alert_to_bot(alert_payload: Dict[str, Any]) -> bool:
    """
    Asynchronicznie wysyła alert do bot_service.

    Args:
        alert_payload: Dict z kluczami:
            - event_id
            - signal_id
            - symbol
            - direction
            - entry, sl, tp
            - risk_usdt, risk_pct, rr
            - structure_state
            - raw_context

    Returns:
        True jeśli wysłano pomyślnie, False w przypadku błędu
    """
    symbol = alert_payload.get("symbol", "UNKNOWN")
    event_id = alert_payload.get("event_id", "UNKNOWN")
    timeout = _client_timeout()
    max_retries = max(1, int(os.environ.get("BOT_SERVICE_RETRIES", "3")))
    base_delay = float(os.environ.get("BOT_SERVICE_RETRY_BASE_SEC", "0.75"))

    for attempt in range(1, max_retries + 1):
        s = _session
        if s is None or s.closed:
            await _reset_session()
        try:
            session = await _get_session()
            async with session.post(
                BOT_SERVICE_URL,
                json=alert_payload,
                timeout=timeout,
            ) as resp:
                if resp.status == 200:
                    score = alert_payload.get("raw_context", {}).get("confidence_score", 0)
                    logger.info(
                        f"🚀 SYGNAŁ_OK | ALERT SENT SUCCESSFULLY [integration] {symbol} {alert_payload['direction']} "
                        f"| event_id={event_id} | score={score:.1f}"
                    )
                    return True
                text = await resp.text()
                logger.error(f"❌ Bot Service returned {resp.status} for {symbol}: {text}")
                return False

        except asyncio.TimeoutError as e:
            logger.warning(
                f"⏱️ Timeout sending alert for {symbol} (event_id={event_id}) "
                f"attempt {attempt}/{max_retries} — bot_service nie odpowiedział w "
                f"{timeout.total}s (zwiększ BOT_SERVICE_TIMEOUT_SEC jeśli Bybit/Firestore są wolne)"
            )
            if attempt < max_retries:
                await _reset_session()
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
                continue
            logger.error(
                f"❌ Timeout po {max_retries} próbach dla {symbol} event_id={event_id}: {e}"
            )
            return False

        except aiohttp.ClientError as e:
            logger.warning(
                f"⚠️ Sieć/HTTP przy wysyłce alertu {symbol} (event_id={event_id}) "
                f"attempt {attempt}/{max_retries}: {e}"
            )
            if attempt < max_retries:
                await _reset_session()
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
                continue
            logger.error(f"❌ Network error sending alert for {symbol}: {e}")
            return False

        except Exception as e:
            logger.exception(f"❌ Unexpected error sending alert for {symbol}: {e}")
            return False

    return False
