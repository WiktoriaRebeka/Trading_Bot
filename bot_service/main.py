
# Lokalizacja: bot_service/main.py
import os
import logging

from shared_lib.config_loader import load_config
load_config()

from flask import Flask
from bot_service.app_setup import initialize_app_services, register_endpoints

# Inicjalizacja podstawowego logowania
logging.basicConfig(level=logging.INFO)
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

# Gunicorn szuka tej zmiennej
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
