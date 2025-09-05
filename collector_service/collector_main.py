# Lokalizacja: collector_service/collector_main.py (ZASTĄP CAŁY PLIK)

import logging
import sys
import os

# Wstawienie ścieżki
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Minimalne importy
from flask import Flask
import google.cloud.logging

# Konfiguracja logowania
try:
    google.cloud.logging.Client().setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud (collector_service) skonfigurowane.")
except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e}")

# Import logiki z nowego pliku
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