# trading_bot/collector_service/data_collector.py

import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional

from shared_lib import firebase_client
from shared_lib import constants

# Uzyskujemy logger na poziomie modułu
logger = logging.getLogger(__name__)

async def _fetch_kline_for_symbol(session: aiohttp.ClientSession, symbol: str) -> Optional[Dict[str, Any]]:
    api_symbol = symbol.replace('.P', '')
    params = {"category": "linear", "symbol": api_symbol, "interval": "1", "limit": 2}
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=3) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
                    kline_list = data["result"]["list"]
                    target_kline = kline_list[1] if len(kline_list) > 1 else kline_list[0]
                    return {"symbol": symbol, "high": float(target_kline[2]), "low": float(target_kline[3]), "close": float(target_kline[4]), "kline_timestamp": int(target_kline[0])}
                else:
                    logger.warning(f"[{symbol}] API zwróciło błąd: {data.get('retMsg', 'Brak wiadomości')}")
        except Exception as e:
            logger.warning(f"[{symbol}] Błąd w _fetch_kline_for_symbol (próba {attempt+1}): {e}")
    logger.error(f"[{symbol}] Nie udało się pobrać danych po {max_retries} próbach.")
    return None

async def get_latest_klines_for_all_symbols(symbols_to_watch: List[str]) -> Dict[str, Dict[str, Any]]:
    logger.info(f"Pobieranie klines dla {len(symbols_to_watch)} symboli.")
    if not symbols_to_watch:
        logger.warning("Lista symboli do obserwacji jest pusta.")
        return {}
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_kline_for_symbol(session, symbol) for symbol in symbols_to_watch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    klines_data = {result['symbol']: result for result in results if result is not None and not isinstance(result, Exception)}
    logger.info(f"Pomyślnie pobrano dane kline dla {len(klines_data)}/{len(symbols_to_watch)} symboli.")
    return klines_data

def get_symbols_to_watch_from_config() -> List[str]:
    logger.info("Pobieranie konfiguracji symboli z Firestore.")
    try:
        db = firebase_client.get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            symbols = doc.to_dict().get("symbols_to_watch", [])
            if isinstance(symbols, list) and symbols:
                logger.info(f"Znaleziono {len(symbols)} symboli w konfiguracji.")
                return symbols
        logger.error(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' jest pusty, nie istnieje lub nie zawiera listy 'symbols_to_watch'.")
        return []
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas odczytu konfiguracji symboli: {e}", exc_info=True)
        # Rzucamy wyjątek dalej, aby endpoint mógł go złapać i zalogować
        raise

def save_klines_to_firestore(klines_data: Dict[str, Dict[str, Any]]):
    if not klines_data:
        logger.info("Brak nowych danych kline do zapisania.")
        return
    logger.info(f"Zapisywanie {len(klines_data)} rekordów kline do Firestore.")
    db = firebase_client.get_db()
    batch = db.batch()
    for symbol, data in klines_data.items():
        doc_ref = db.collection(constants.LATEST_KLINES_COLLECTION).document(symbol)
        batch.set(doc_ref, data, merge=True)
    try:
        batch.commit()
        logger.info("Zapis batchowy do Firestore zakończony sukcesem.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu batchowego do Firestore: {e}", exc_info=True)
        raise

async def run_data_collection_cycle():
    # Ta funkcja nie musi logować, bo funkcje, które woła, robią to szczegółowo.
    symbols_to_watch = get_symbols_to_watch_from_config()
    if not symbols_to_watch:
        return "Brak symboli do przetworzenia w konfiguracji.", 200
    klines = await get_latest_klines_for_all_symbols(symbols_to_watch)
    save_klines_to_firestore(klines)
    return "Cykl kolektora danych zakończony pomyślnie.", 200