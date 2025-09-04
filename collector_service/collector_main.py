# Lokalizacja: collector_service/collector_main.py (ZASTĄP CAŁY PLIK)

import logging
import sys
import os

# KROK 1: Załaduj konfigurację PRZED importem jakichkolwiek modułów aplikacji.
from shared_lib.config_loader import load_config
load_config()

# KROK 2: Skonfiguruj logowanie.
try:
    import google.cloud.logging
    google.cloud.logging.Client().setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud (collector_service) skonfigurowane.")
except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e}")

# KROK 3: Teraz, gdy środowisko jest gotowe, importuj resztę aplikacji.
from flask import Flask
from collector_service.app_setup import initialize_app_services, register_endpoints

def create_app():
    """Tworzy i zwraca aplikację Flask dla kolektora."""
    app = Flask(__name__)

    initialize_app_services(app)
    register_endpoints(app)

    return app

# Gunicorn szuka tej zmiennej
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)