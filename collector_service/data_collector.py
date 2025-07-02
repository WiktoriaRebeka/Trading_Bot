# W pliku: /data_collector.py
# ZASTĄP CAŁĄ ZAWARTOŚĆ PLIKU

import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional

import firebase_client
import constants

logger = logging.getLogger(__name__)

async def _fetch_kline_for_symbol(session: aiohttp.ClientSession, symbol: str) -> Optional[Dict[str, Any]]:
    """Asynchronicznie pobiera dane kline dla jednego symbolu z mechanizmem retry."""
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
                    
                    return {
                        "symbol": symbol,
                        "high": float(target_kline[2]),
                        "low": float(target_kline[3]),
                        "close": float(target_kline[4]),
                        "kline_timestamp": int(target_kline[0])
                    }
                else:
                    logger.warning(f"[{symbol}] API zwróciło błąd: {data.get('retMsg', 'Brak wiadomości')}")
                    
        except asyncio.TimeoutError:
            logger.warning(f"[{symbol}] Próba {attempt + 1}/{max_retries} nieudana (timeout).")
        except aiohttp.ClientError as e:
            logger.warning(f"[{symbol}] Próba {attempt + 1}/{max_retries} nieudana (błąd klienta): {e}")
        
        if attempt < max_retries - 1:
            await asyncio.sleep(0.5) # Krótka przerwa przed ponowieniem
            
    logger.error(f"[{symbol}] Nie udało się pobrać danych po {max_retries} próbach.")
    return None

async def get_latest_klines_for_all_symbols(symbols_to_watch: List[str]) -> Dict[str, Dict[str, Any]]:
    """Asynchronicznie i równolegle pobiera dane dla wszystkich podanych symboli."""
    if not symbols_to_watch:
        logger.warning("Lista symboli do obserwacji jest pusta. Pomijam pobieranie danych.")
        return {}

    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_kline_for_symbol(session, symbol) for symbol in symbols_to_watch]
        results = await asyncio.gather(*tasks)
    
    # Filtruj wyniki, które zwróciły None (błędy) i przekształć listę w słownik
    klines_data = {result['symbol']: result for result in results if result is not None}
    
    if klines_data:
        logger.info(f"Pomyślnie pobrano dane kline dla {len(klines_data)}/{len(symbols_to_watch)} symboli.")
    
    return klines_data

def get_symbols_to_watch_from_config() -> List[str]:
    """Pobiera listę symboli do obserwacji z dedykowanego dokumentu konfiguracyjnego w Firestore."""
    try:
        db = firebase_client.get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            symbols = doc.to_dict().get("symbols_to_watch", [])
            if isinstance(symbols, list) and symbols:
                logger.info(f"Pobrano {len(symbols)} symboli do obserwacji z konfiguracji Firestore.")
                return symbols
        logger.error(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' jest pusty lub nie istnieje.")
        return []
    except Exception as e:
        logger.error(f"Błąd podczas odczytu konfiguracji symboli z Firestore: {e}", exc_info=True)
        return []

def save_klines_to_firestore(klines_data: Dict[str, Dict[str, Any]]):
    """Zapisuje pobrane dane kline do dedykowanej kolekcji w Firestore."""
    if not klines_data:
        logger.info("Brak nowych danych kline do zapisania w Firestore.")
        return
    db = firebase_client.get_db()
    batch = db.batch()
    for symbol, data in klines_data.items():
        doc_ref = db.collection(constants.LATEST_KLINES_COLLECTION).document(symbol)
        batch.set(doc_ref, data, merge=True)
    try:
        batch.commit()
        logger.info(f"Pomyślnie zapisano/zaktualizowano dane kline dla {len(klines_data)} symboli.")
    except Exception as e:
        logger.error(f"Błąd podczas zapisu batchowego do Firestore: {e}", exc_info=True)

async def run_data_collection_cycle():
    """Główna asynchroniczna funkcja logiki kolektora."""
    logger.info("--- ROZPOCZĘCIE ASYNCHRONICZNEGO CYKLU KOLEKTORA DANYCH ---")
    symbols_to_watch = get_symbols_to_watch_from_config()
    if not symbols_to_watch:
        return "Brak symboli do przetworzenia.", 200
        
    klines = await get_latest_klines_for_all_symbols(symbols_to_watch)
    save_klines_to_firestore(klines)
    
    return "Data collection cycle finished successfully.", 200
