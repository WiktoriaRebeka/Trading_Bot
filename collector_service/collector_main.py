#trading_bot/collector_service/collector_main.py

# Test collector deploy.
import logging
import sys
import asyncio
import os
# Pusty plik na początku - importujemy tylko to, co niezbędne na poziomie globalnym.

def create_app():
    """
    Tworzy i konfiguruje instancję aplikacji Flask (wzorzec Application Factory).
    Ta funkcja jest wywoływana przez Gunicorna w każdym procesie workera.
    """
    # Krok 1: Załaduj konfigurację WEWNĄTRZ fabryki.
    from config_loader import load_config
    load_config()
    
    # Krok 2: Skonfiguruj logowanie.
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [COLLECTOR] - %(message)s'
    )
    
    # Krok 3: Stwórz instancję aplikacji Flask.
    # ### POPRAWKA: DODANO BRAKUJĄCE IMPORTY ###
    from flask import Flask, jsonify
    app = Flask(__name__)
    
    # Krok 4: Zainicjuj klientów WEWNĄTRZ fabryki.
    from firebase_client import initialize_firebase
    initialize_firebase()
    
    # Krok 5: Zaimportuj i zarejestruj endpointy.
    from data_collector import run_data_collection_cycle

    @app.route('/')
    def health_check():
        return "Data Collector Service is running.", 200

    @app.route('/run-collector-cycle', methods=['GET', 'POST'])
    def run_collector_endpoint():
        logger = logging.getLogger(__name__)
        from firebase_client import db_client as firebase_db_client # Lokalny import
        if not firebase_db_client:
            logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
            return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
        try:
            message, status_code = asyncio.run(run_data_collection_cycle())
            logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
            return jsonify({"status": "success", "details": message}), status_code
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
            
    # Krok 6: Zwróć gotową aplikację.
    return app

# Jeśli chcesz uruchomić aplikację lokalnie do testów, możesz dodać:
if __name__ == '__main__':
    # Ta sekcja jest ignorowana przez Gunicorna, ale przydatna do lokalnego debugowania.
    app = create_app()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
