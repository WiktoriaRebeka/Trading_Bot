# Lokalizacja: bot_service/main.py

import sys
import os
import traceback

# --- BARDZO WAŻNE: Dodajemy ścieżkę jako PIERWSZĄ operację ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- LOGOWANIE AWARYJNE ---
# Używamy print do stderr, ponieważ to zadziała nawet, jeśli biblioteka logging zawiedzie.
print("DEBUG: bot_service/main.py - Start pliku.", file=sys.stderr)

# Importujemy tylko to, co jest absolutnie konieczne na poziomie globalnym
from flask import Flask

print("DEBUG: bot_service/main.py - Flask zaimportowany.", file=sys.stderr)

# Importujemy naszą logikę setupu
from bot_service.app_setup import initialize_app_services, register_endpoints

print("DEBUG: bot_service/main.py - app_setup zaimportowany.", file=sys.stderr)


def create_app():
    """Tworzy i zwraca aplikację Flask, ale cała logika jest w app_setup."""
    print("DEBUG: bot_service/main.py - Wewnątrz create_app().", file=sys.stderr)
    
    app = Flask(__name__)
    print("DEBUG: bot_service/main.py - Instancja Flask utworzona.", file=sys.stderr)
    
    try:
        # Inicjalizujemy usługi (Firebase, BigQuery)
        initialize_app_services(app)
        print("DEBUG: bot_service/main.py - initialize_app_services() wykonane.", file=sys.stderr)
        
        # Rejestrujemy endpointy (/health, /run-bot-cycle, etc.)
        register_endpoints(app)
        print("DEBUG: bot_service/main.py - register_endpoints() wykonane.", file=sys.stderr)
        
    except Exception as e:
        # Jeśli jakikolwiek błąd wystąpi wewnątrz create_app, zalogujemy go tutaj.
        print(f"FATAL: Błąd wewnątrz create_app(): {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # Rzucamy wyjątek dalej, aby Gunicorn wiedział, że coś poszło nie tak.
        raise

    print("DEBUG: bot_service/main.py - Zwracam obiekt app z create_app().", file=sys.stderr)
    return app

# Gunicorn szuka tej zmiennej.
print("DEBUG: bot_service/main.py - Zamierzam wywołać create_app().", file=sys.stderr)
try:
    app = create_app()
    print("DEBUG: bot_service/main.py - Zmienna 'app' została pomyślnie utworzona.", file=sys.stderr)
except Exception as e:
    print(f"FATAL: Wywołanie create_app() na poziomie globalnym nie powiodło się: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    # Ustawiamy app na None, aby Gunicorn na pewno się wywalił z czytelnym błędem, jeśli do tego dojdzie.
    app = None
    # Celowo zatrzymujemy proces, jeśli nie uda się stworzyć aplikacji.
    sys.exit(1)


if __name__ == '__main__':
    # Uruchomienie lokalne
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)