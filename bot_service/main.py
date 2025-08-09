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
    
    try:
        # Wywołujemy inicjalizację bezpośrednio, tak jak na początku,
        # ale teraz, gdy inne błędy importu są naprawione, to powinno zadziałać.
        initialize_app_services(app)
        register_endpoints(app)
    except Exception as e:
        logger.critical(f"FATAL: Błąd podczas tworzenia aplikacji Flask: {e}", exc_info=True)
        # Jeśli inicjalizacja zawiedzie, flaga błędu zostanie ustawiona wewnątrz
        # initialize_app_services, a aplikacja i tak wystartuje w stanie "unhealthy".
        
    return app

# Ta linia jest kluczowa dla Gunicorna. Wywołuje create_app() i tworzy instancję 'app'.
app = create_app()

# Ten blok jest używany tylko do lokalnego uruchamiania.
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)