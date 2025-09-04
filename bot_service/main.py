# Lokalizacja: bot_service/main.py
import os
import logging

try:
    import google.cloud.logging
    client = google.cloud.logging.Client()
    client.setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud (bot_service) skonfigurowane pomyślnie.")
except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e}")

from shared_lib.config_loader import load_config
load_config()

from flask import Flask
from bot_service.app_setup import initialize_app_services, register_endpoints

logger = logging.getLogger(__name__)

def create_app():
    """Tworzy i konfiguruje aplikację Flask."""
    app = Flask(__name__)

    try:
        initialize_app_services(app)
        register_endpoints(app)
    except Exception as e:
        logger.critical(f"FATAL: Błąd podczas tworzenia aplikacji Flask: {e}", exc_info=True)
        raise

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)