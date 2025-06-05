# TRADING_BOT/simple_grpc_test.py
import logging
import sys
import time

# Konfiguracja logowania, aby wysyłać logi do stdout, co App Engine powinien przechwycić
logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("simple_grpc_test_script") # Użyj konkretnej nazwy loggera

logger.info(f"[SIMPLE_GRPC_TEST] START skryptu. Python version: {sys.version}. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

try:
    logger.info("[SIMPLE_GRPC_TEST] Próba importu grpc...")
    import grpc
    # Sprawdź, czy moduł został poprawnie załadowany i ma atrybut __version__
    if hasattr(grpc, '__version__'):
        logger.info(f"[SIMPLE_GRPC_TEST] Import grpc ZAKOŃCZONY SUKCESEM. Wersja grpc: {grpc.__version__}")
    else:
        logger.warning("[SIMPLE_GRPC_TEST] Import grpc WYKONANY, ale brak atrybutu __version__.")
        logger.info(f"[SIMPLE_GRPC_TEST] Typ obiektu grpc: {type(grpc)}")
        logger.info(f"[SIMPLE_GRPC_TEST] Zawartość dir(grpc): {dir(grpc)}")

except ImportError as ie:
    logger.error(f"[SIMPLE_GRPC_TEST] BŁĄD IMPORTU grpc: {ie}", exc_info=True)
    # Dodatkowe logowanie, które może pomóc zdiagnozować problemy z natywnymi rozszerzeniami
    if "No module named 'grpc._cython'" in str(ie) or "cannot import name" in str(ie) or "_implementation" in str(ie):
        logger.error("[SIMPLE_GRPC_TEST] Podejrzenie problemu z natywnymi rozszerzeniami grpcio lub jego wewnętrznymi zależnościami w tym środowisku.")
except Exception as e:
    logger.error(f"[SIMPLE_GRPC_TEST] INNY BŁĄD podczas testu grpc: {e}", exc_info=True)
finally:
    logger.info(f"[SIMPLE_GRPC_TEST] KONIEC skryptu. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    # Skrypt zakończy działanie. Instancja App Engine (jeśli min_instances > 0) pozostanie,
    # ale ten skrypt nie będzie już działał. Logi powinny być dostępne.