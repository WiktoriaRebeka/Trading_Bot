# TRADING_BOT/simple_grpc_test.py
import logging
import sys
import time
import os # Dodajmy os do sprawdzenia zmiennych środowiskowych

# Konfiguracja logowania, aby wysyłać logi do stdout, co App Engine powinien przechwycić
logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("simple_grpc_test_script")

logger.info(f"--- [SIMPLE_GRPC_TEST] START ---")
logger.info(f"Python version: {sys.version}")
logger.info(f"Platform: {sys.platform}")
logger.info(f"GAE_ENV: {os.getenv('GAE_ENV')}")
logger.info(f"GAE_RUNTIME: {os.getenv('GAE_RUNTIME')}")
logger.info(f"GAE_VERSION: {os.getenv('GAE_VERSION')}")
logger.info(f"CWD: {os.getcwd()}")
logger.info(f"PYTHONPATH: {os.getenv('PYTHONPATH')}")
logger.info(f"sys.path: {sys.path}")
logger.info(f"Czas UTC: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

try:
    logger.info("Próba importu 'grpc'...")
    import grpc
    logger.info("Import 'grpc' ZAKOŃCZONY SUKCESEM.")
    
    # Spróbujmy uzyskać wersję na kilka sposobów
    grpc_version = "Nie udało się ustalić wersji"
    if hasattr(grpc, '__version__'):
        grpc_version = grpc.__version__
    elif hasattr(grpc, 'version'): # Czasem jest bez podkreśleń
        grpc_version = grpc.version
        
    logger.info(f"Wersja grpc: {grpc_version}")
    logger.info(f"Typ obiektu grpc: {type(grpc)}")
    # logger.info(f"Zawartość dir(grpc): {dir(grpc)}") # Może być bardzo długie, odkomentuj w razie potrzeby

    # Dodatkowy test - czy można utworzyć prosty kanał (bez łączenia się)
    # To może ujawnić problemy z ładowaniem wewnętrznych komponentów grpc
    try:
        logger.info("Próba utworzenia grpc.insecure_channel('localhost:12345')...")
        channel = grpc.insecure_channel('localhost:12345') # Nie łączymy się, tylko tworzymy obiekt
        logger.info(f"Utworzono obiekt kanału: {type(channel)}")
        # channel.close() # Zamknij kanał jeśli to test połączenia, tu tylko tworzenie obiektu
    except Exception as channel_e:
        logger.error(f"BŁĄD podczas próby utworzenia grpc.insecure_channel: {channel_e}", exc_info=True)

except ImportError as ie:
    logger.error(f"BŁĄD IMPORTU 'grpc': {ie}", exc_info=True)
    if "No module named 'grpc._cython'" in str(ie) or \
       "cannot import name" in str(ie) or \
       "_implementation" in str(ie) or \
       "expected schweren Fehler" in str(ie).lower(): # Czasem są komunikaty w innym języku
        logger.error("Podejrzenie problemu z natywnymi rozszerzeniami grpcio lub jego wewnętrznymi zależnościami w tym środowisku.")
except Exception as e:
    logger.error(f"INNY BŁĄD podczas testu grpc: {e}", exc_info=True)
finally:
    logger.info(f"--- [SIMPLE_GRPC_TEST] KONIEC --- Czas UTC: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    # Aby instancja nie zakończyła się natychmiast i dała czas na zebranie logów,
    # możesz dodać time.sleep(300) # Czekaj 5 minut, jeśli jest to jedyny proces
    # Ale przy min_instances: 1, instancja powinna pozostać.