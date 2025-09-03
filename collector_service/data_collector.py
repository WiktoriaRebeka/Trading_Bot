# Lokalizacja: collector_service/data_collector.py 
import logging
import asyncio
import aiohttp
import os  # <--- DODAJ TĘ LINIĘ
from typing import List, Dict, Any, Optional


import google.auth
import google.auth.transport.requests
import google.oauth2.id_token

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

# --- NOWA FUNKCJA WYZWALAJĄCA ---
async def _trigger_bot_service_cycle(cycle_id: str):
    """
    Wywołuje endpoint /run-bot-cycle w usłudze trading-bot-service.
    Działa jako wewnętrzny, niezawodny trigger.
    """
    log_extra = {"json_fields": {"cycle_id": cycle_id}}
    
    # Pobieramy URL docelowej usługi ze zmiennej środowiskowej
    target_url = os.getenv("TRADING_BOT_SERVICE_URL")
    if not target_url:
        logger.error("Zmienna środowiskowa TRADING_BOT_SERVICE_URL nie jest ustawiona! Nie można wyzwolić cyklu bota.", extra=log_extra)
        return

    try:
        logger.info(f"Próba wyzwolenia cyklu bota w usłudze: {target_url}", extra=log_extra)
        
        # Uzyskujemy token tożsamości OIDC do bezpiecznego wywołania innej usługi Cloud Run
        creds, project = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        id_token = google.oauth2.id_token.fetch_id_token(auth_req, target_url)
        
        headers = {
            "Authorization": f"Bearer {id_token}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(target_url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    logger.info(f"Pomyślnie wyzwolono cykl bota. Status: {response.status}", extra=log_extra)
                else:
                    responseText = await response.text()
                    logger.error(f"Błąd podczas wyzwalania cyklu bota. Status: {response.status}, Odpowiedź: {responseText}", extra=log_extra)

    except Exception as e:
        logger.error(f"Krytyczny błąd podczas próby wyzwolenia cyklu bota: {e}", exc_info=True, extra=log_extra)


async def run_data_collection_cycle(cycle_id: str) -> (str, int):
    log_extra = {"json_fields": {"cycle_id": cycle_id}}
    
    symbols_to_watch = get_symbols_to_watch_from_config()
    if not symbols_to_watch:
        logger.warning("Brak symboli do przetworzenia w konfiguracji.")
        # Mimo braku symboli, nadal próbujemy wyzwolić bota, bo może mieć inne zadania
        await _trigger_bot_service_cycle(cycle_id)
        return "Brak symboli do przetworzenia, ale cykl bota został wyzwolony.", 200
        
    klines = await get_latest_klines_for_all_symbols(symbols_to_watch, cycle_id)
    
    if klines:
        save_klines_to_firestore(klines, cycle_id)
    
    # Po wykonaniu głównego zadania, wyzwalamy cykl bota analitycznego
    # Używamy asyncio.create_task, aby zrobić to w tle i nie czekać na odpowiedź
    asyncio.create_task(_trigger_bot_service_cycle(cycle_id))
    
    # --- POPRAWIONA LINIA ---
    return f"Cykl kolektora zakończony. Przetworzono {len(klines)}/{len(symbols_to_watch)} symboli. Cykl bota został wyzwolony.", 200