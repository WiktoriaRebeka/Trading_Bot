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
    # Rejestrujemy funkcję inicjalizującą, ale jej nie wywołujemy od razu.
    # Flask wykona ją automatycznie przed obsłużeniem pierwszego żądania.
    @app.before_first_request
    def setup_services():
        try:
            initialize_app_services(app)
        except Exception as e:
            logger.critical(f"FATAL: Błąd podczas inicjalizacji usług: {e}", exc_info=True)
            # Ustawiamy flagę błędu, aby endpointy wiedziały, że coś poszło nie tak
            app.config['INITIALIZATION_SUCCESS'] = False
            app.config['INITIALIZATION_FAILURE_REASON'] = str(e)

    # Rejestracja endpointów odbywa się od razu.
    register_endpoints(app)
    # === KONIEC ZMIAN ===

    return app

app = create_app()

# Ten blok jest potrzebny tylko do lokalnego uruchamiania, zostawiamy go bez zmian.
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)