# Lokalizacja: bot_service/main.py (ZASTĄP CAŁY PLIK)

import logging
import sys
import os

# Krok 1: Wstawienie ścieżki - to jest krytyczne, aby było na samej górze.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Krok 2: Importy minimalne, absolutnie niezbędne do stworzenia aplikacji.
from flask import Flask
import google.cloud.logging

# Krok 3: Konfiguracja logowania - jedyna operacja globalna.
try:
    google.cloud.logging.Client().setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud (bot_service) skonfigurowane.")
except Exception as e:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e}")


# Importujemy naszą logikę setupu
from bot_service.app_setup import initialize_app_services, register_endpoints

def create_app():
    """Tworzy i zwraca aplikację Flask, ale cała logika jest w app_setup."""
    app = Flask(__name__)
    
    # Inicjalizujemy usługi (Firebase, BigQuery)
    initialize_app_services(app)
    
    # Rejestrujemy endpointy (/health, /run-bot-cycle, etc.)
    register_endpoints(app)
    
    return app

# Gunicorn szuka tej zmiennej.
app = create_app()

if __name__ == '__main__':
    # Uruchomienie lokalne
    port = int(os.environ.get("PORT", 8080))
    # Użycie `app.run` jest tylko dla deweloperki. Gunicorn uruchamia `app` bezpośrednio.
    app.run(host='0.0.0.0', port=port, debug=False)