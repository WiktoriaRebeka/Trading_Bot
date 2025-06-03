import logging
import sys
import time

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info(f"[SIMPLE_GRPC_TEST] START skryptu. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
try:
    logger.info("[SIMPLE_GRPC_TEST] Próba importu grpc...")
    import grpc
    logger.info(f"[SIMPLE_GRPC_TEST] Import grpc ZAKOŃCZONY SUKCESEM. Wersja grpc: {grpc.__version__}")

    # Można dodać próbę stworzenia prostego kanału (nie połączy się, ale sprawdzi inicjalizację)
    # logger.info("[SIMPLE_GRPC_TEST] Próba stworzenia prostego, niezabezpieczonego kanału grpc (nie połączy się)...")
    # channel = grpc.insecure_channel('localhost:12345')
    # logger.info(f"[SIMPLE_GRPC_TEST] Stworzenie kanału grpc ZAKOŃCZONE SUKCESEM (kanał: {channel})")

except ImportError as ie:
    logger.error(f"[SIMPLE_GRPC_TEST] BŁĄD IMPORTU grpc: {ie}", exc_info=True)
except Exception as e:
    logger.error(f"[SIMPLE_GRPC_TEST] INNY BŁĄD podczas testu grpc: {e}", exc_info=True)
finally:
    logger.info(f"[SIMPLE_GRPC_TEST] KONIEC skryptu. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

# Wymuszenie zakończenia, aby App Engine nie próbował uruchomić serwera WWW
# sys.exit(0) # Może nie być konieczne, jeśli entrypoint to tylko wykonanie skryptu