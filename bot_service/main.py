# Lokalizacja: bot_service/main.py
import os
import logging
from flask import Flask

from bot_service.app_setup import initialize_app_services, register_endpoints

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    """Tworzy i konfiguruje aplikację Flask."""
    app = Flask(__name__)
    
    # === POCZĄTEK ZMIAN ===
    # Inicjalizacja jest teraz wywoływana bezpośrednio w fabryce.
    # To jest nowoczesne i zalecane podejście.
    try:
        initialize_app_services(app)
        register_endpoints(app)
    except Exception as e:
        logger.critical(f"FATAL: Błąd podczas tworzenia aplikacji Flask: {e}", exc_info=True)
        # W przypadku błędu, aplikacja i tak musi zwrócić obiekt 'app',
        # aby Gunicorn mógł obsłużyć błąd. Flagi błędu są ustawiane wewnątrz initialize_app_services.
    # === KONIEC ZMIAN ===

    return app

# Tworzymy instancję aplikacji do użycia przez Gunicorn
app = create_app()

# Ten blok jest używany tylko do lokalnego uruchamiania
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)