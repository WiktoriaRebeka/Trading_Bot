# /trading_bot/data_collector.py (WERSJA FINALNA z Poprawnymi Importami)

import logging
import requests
import time
from typing import List, Dict, Any

# Poprawione, bezwzględne importy
import firebase_client
import constants

logger = logging.getLogger("app.data_collector")

# === LISTA SYMBOLI DO OBSERWACJI ===
SYMBOLS_TO_WATCH = [
    "QNTUSDT.P", "TIAUSDT.P", "FILUSDT.P", "ATOMUSDT.P", "ICPUSDT.P",
    "AAVEUSDT.P", "APTUSDT.P", "NEARUSDT.P", "TAOUSDT.P", "UNIUSDT.P",
    "XMRUSDT.P", "DOTUSDT.P", "HYPEUSDT.P", "TONUSDT.P", "AVAXUSDT.P",
    "LINKUSDT.P", "SUIUSDT.P", "SOLUSDT.P", "XRPUSDT.P", "LTCUSDT.P"
]

def get_latest_klines_for_all_symbols() -> Dict[str, Dict[str, Any]]:
    """Pobiera ostatnią świecę dla wszystkich zdefiniowanych symboli z mechanizmem retry."""
    
    klines_data = {}
    for symbol in SYMBOLS_TO_WATCH:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol, "interval": "1", "limit": 2}
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=3)
                response.raise_for_status()
                data = response.json()
                if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
                    kline_list = data["result"]["list"]
                    target_kline = kline_list[1] if len(kline_list) > 1 else kline_list[0]
                    
                    klines_data[symbol] = {
                        "high": float(target_kline[2]),
                        "low": float(target_kline[3]),
                        "close": float(target_kline[4]),
                        "kline_timestamp": int(target_kline[0])
                    }
                    break 
            except Exception as e:
                logger.warning(f"[{symbol}] Próba {attempt + 1}/{max_retries} nieudana: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                else:
                    logger.error(f"[{symbol}] Nie udało się pobrać danych po {max_retries} próbach.")
    return klines_data

def save_klines_to_firestore(klines_data: Dict[str, Dict[str, Any]]):
    """Zapisuje pobrane dane kline do dedykowanej kolekcji w Firestore."""
    if not klines_data:
        logger.warning("Brak danych kline do zapisania.")
        return

    db = firebase_client.get_db()
    batch = db.batch()
    
    for symbol, data in klines_data.items():
        # nowa, poprawna linia
        doc_ref = db.collection(constants.LATEST_KLINES_COLLECTION).document(symbol)
        batch.set(doc_ref, data, merge=True)
    
    batch.commit()
    logger.info(f"Pomyślnie zapisano/zaktualizowano dane kline dla {len(klines_data)} symboli.")

def run_data_collection_cycle(request=None):
    """
    Główna funkcja wywoływana przez Cloud Scheduler.
    `request` jest potrzebny dla triggera HTTP w Cloud Functions.
    """
    # Inicjalizacja Firebase wewnątrz funkcji, aby zapewnić, że jest wykonana przy każdym wywołaniu
    if not firebase_client.db_client:
        firebase_client.initialize_firebase()

    logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH ---")
    try:
        klines = get_latest_klines_for_all_symbols()
        save_klines_to_firestore(klines)
        logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
        return "Data collection cycle finished successfully.", 200
    except Exception as e:
        logger.error(f"Krytyczny błąd w cyklu kolektora danych: {e}", exc_info=True)
        return "Error during data collection cycle.", 500

# Ten blok jest tylko do testów lokalnych i nie jest używany w chmurze
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_data_collection_cycle()