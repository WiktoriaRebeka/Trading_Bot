# Lokalizacja: bot_service/main.py

import os
import logging
import google.cloud.logging

# KROK 1: Konfiguracja logowania PRZED WSZYSTKIM INNYM
try:
    client = google.cloud.logging.Client()
    client.setup_logging()
    # Ten log powinien być zawsze widoczny jako pierwszy przy starcie kontenera
    logging.info("--- [MAIN] Ustrukturyzowane logowanie Google Cloud skonfigurowane. ---")
except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"--- [MAIN] Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e} ---")

# KROK 2: Importy modułów aplikacji DOPIERO PO skonfigurowaniu logowania
from flask import Flask
from shared_lib.config_loader import load_config
from bot_service.app_setup import initialize_app_services, register_endpoints

# Tworzymy loggera, którego będziemy używać w tym pliku
logger = logging.getLogger(__name__)

def create_app():
    """
    Tworzy i konfiguruje instancję aplikacji Flask (wzorzec Application Factory).
    """
    logger.info("--- [MAIN] Rozpoczynam tworzenie aplikacji Flask (create_app)... ---")
    
    # Załaduj konfigurację (np. zmienne środowiskowe)
    load_config()
    
    app = Flask(__name__)

    try:
        # Przekaż obiekt 'app' do funkcji konfiguracyjnych
        initialize_app_services(app)
        register_endpoints(app)
        logger.info("--- [MAIN] Aplikacja Flask pomyślnie skonfigurowana. ---")
    except Exception as e:
        logger.critical(f"--- [MAIN] FATAL: Błąd podczas tworzenia aplikacji Flask: {e} ---", exc_info=True)
        raise

    return app

# Gunicorn szuka tej zmiennej. Wywołujemy fabrykę, aby ją utworzyć.
app = create_app()
logger.info("--- [MAIN] Instancja aplikacji 'app' utworzona i gotowa do przekazania do Gunicorna. ---")


if __name__ == '__main__':
    # Ta sekcja jest używana tylko do uruchomienia lokalnego (np. python bot_service/main.py)
    port = int(os.environ.get("PORT", 8080))
    # Używamy debug=True lokalnie, aby uzyskać lepsze komunikaty o błędach
    app.run(host='0.0.0.0', port=port, debug=True)