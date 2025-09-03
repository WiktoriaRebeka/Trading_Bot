import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional

from shared_lib import constants
from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config

logger = logging.getLogger(__name__)

async def _fetch_kline_for_symbol(session: aiohttp.ClientSession, symbol: str, cycle_id: str) -> Optional[Dict[str, Any]]:
    """Pobiera najnowszą, zamkniętą świecę 1-minutową dla danego symbolu."""
    api_symbol = symbol.replace('.P', '')
    # --- KLUCZOWA ZMIANA: Powrót do interwału 1-minutowego ---
    params = {"category": "linear", "symbol": api_symbol, "interval": "1", "limit": 2}
    max_retries = 3
    
    log_extra = {"json_fields": {"cycle_id": cycle_id, "symbol": symbol}}

    for attempt in range(max_retries):
        try:
            kline_endpoint = "/v5/market/kline"
            full_url = constants.BYBIT_API_URL_V5 + kline_endpoint
            async with session.get(full_url, params=params, timeout=5) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
                    kline_list = data["result"]["list"]
                    
                    if len(kline_list) > 1:
                        target_kline = kline_list[1] # Zawsze bierzemy przedostatnią, zamkniętą świecę
                        return {
                            "symbol": symbol, 
                            "high": float(target_kline[2]), 
                            "low": float(target_kline[3]), 
                            "close": float(target_kline[4]), 
                            "timestamp": int(target_kline[0])
                        }
                    else:
                        logger.warning(f"API Bybit zwróciło tylko jedną świecę. Pomijam zapis, aby zapewnić spójność danych.", extra=log_extra)
                        return None
                else:
                    logger.warning(f"API Bybit zwróciło błąd: {data.get('retMsg', 'Brak wiadomości')}", extra=log_extra)
        except Exception as e:
            logger.warning(f"Błąd w _fetch_kline_for_symbol (próba {attempt+1}): {e}", extra=log_extra)
            await asyncio.sleep(0.5) 
            
    logger.error(f"Nie udało się pobrać danych dla {symbol} po {max_retries} próbach.", extra=log_extra)
    return None

async def get_latest_klines_for_all_symbols(symbols_to_watch: List[str], cycle_id: str) -> Dict[str, Dict[str, Any]]:
    log_extra = {"json_fields": {"cycle_id": cycle_id}}
    logger.info(f"Pobieranie klines dla {len(symbols_to_watch)} symboli.")
    
    if not symbols_to_watch:
        logger.warning("Lista symboli do obserwacji jest pusta.")
        return {}
        
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_kline_for_symbol(session, symbol, cycle_id) for symbol in symbols_to_watch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    klines_data = {}
    for result in results:
        if result is not None and not isinstance(result, Exception):
            klines_data[result['symbol']] = result
    
    logger.info(f"Pomyślnie pobrano dane kline dla {len(klines_data)}/{len(symbols_to_watch)} symboli.")
    return klines_data

def save_klines_to_firestore(klines_data: Dict[str, Dict[str, Any]], cycle_id: str):
    log_extra = {"json_fields": {"cycle_id": cycle_id}}
    if not klines_data:
        logger.info("Brak nowych danych kline do zapisania.")
        return
        
    logger.info(f"Zapisywanie {len(klines_data)} rekordów kline do Firestore.")
    db = get_db()
    batch = db.batch()
    
    for symbol, data in klines_data.items():
        doc_ref = db.collection(constants.LATEST_KLINES_COLLECTION).document(symbol)
        batch.set(doc_ref, data, merge=True)
        
    try:
        batch.commit()
        logger.info("Zapis batchowy do Firestore zakończony sukcesem.")
    except Exception as e:
        logger.error(f"Krytyczny błąd podczas zapisu batchowego do Firestore: {e}", exc_info=True, extra=log_extra)
        raise

async def run_data_collection_cycle(cycle_id: str) -> (str, int):
    log_extra = {"json_fields": {"cycle_id": cycle_id}}
    
    symbols_to_watch = get_symbols_to_watch_from_config()
    if not symbols_to_watch:
        logger.warning("Brak symboli do przetworzenia w konfiguracji.")
        return "Brak symboli do przetworzenia w konfiguracji.", 200
        
    klines = await get_latest_klines_for_all_symbols(symbols_to_watch, cycle_id)
    
    if klines:
        save_klines_to_firestore(klines, cycle_id)
    
    return f"Cykl kolektora danych zakończony. Przetworzono {len(klines)}/{len(symbols_to_watch)} symboli.", 200