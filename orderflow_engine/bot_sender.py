# orderflow_engine/bot_sender.py
# WERSJA 1.0 - Asynchroniczna wysyłka alertów do bot_service

import os
import aiohttp
import logging
from typing import Dict, Any

BOT_SERVICE_URL = os.environ.get(
    "BOT_SERVICE_URL",
    "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"
)

logger = logging.getLogger(__name__)
logger.info(f"[bot_sender] BOT_SERVICE_URL={'env' if os.environ.get('BOT_SERVICE_URL') else 'fallback'}: {BOT_SERVICE_URL}")


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
    symbol = alert_payload.get('symbol', 'UNKNOWN')
    event_id = alert_payload.get('event_id', 'UNKNOWN')
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BOT_SERVICE_URL, 
                json=alert_payload, 
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                
                if resp.status == 200:
                    logger.info(
                        f"🚀 ALERT SENT SUCCESSFULLY: {symbol} {alert_payload['direction']} "
                        f"| Event: {event_id} | Score: {alert_payload.get('raw_context', {}).get('confidence_score', 0):.1f}"
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.error(
                        f"❌ Bot Service returned {resp.status} for {symbol}: {text}"
                    )
                    return False
                    
    except aiohttp.ClientError as e:
        logger.error(f"❌ Network error sending alert for {symbol}: {e}")
        return False
    except Exception as e:
        logger.exception(f"❌ Unexpected error sending alert for {symbol}: {e}")
        return False