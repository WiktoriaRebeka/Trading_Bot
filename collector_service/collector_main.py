# Lokalizacja: collector_service/collector_main.py

import logging
import sys
import asyncio
import os

def create_app():
    """
    Tworzy i konfiguruje instancję aplikacji Flask (wzorzec Application Factory).
    To jest najbardziej niezawodny wzorzec dla Gunicorna.
    """
    # Krok 1: Importy WEWNĄTRZ fabryki.
    from flask import Flask, jsonify
    from shared_lib.config_loader import load_config
    from shared_lib.firebase_client import initialize_firebase
    from collector_service.data_collector import run_data_collection_cycle

    # Krok 2: Konfiguracja i inicjalizacja.
    load_config()
    initialize_firebase()

    # Krok 3: Konfiguracja logowania.
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [COLLECTOR] - %(message)s'
    )

    # Krok 4: Stwórz instancję aplikacji Flask.
    app = Flask(__name__)
    logging.info("Aplikacja Flask [collector] została utworzona wewnątrz fabryki.")

    @app.route('/')
    def health_check():
        return "Data Collector Service is running.", 200

    @app.route('/run-collector-cycle', methods=['GET', 'POST'])
    def run_collector_endpoint():
        logger = logging.getLogger(__name__)
        # Lokalny import klienta, aby mieć pewność, że jest zainicjalizowany.
        from shared_lib.firebase_client import db_client
        
        logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH ---")
        if not db_client:
            logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
            return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
        try:
            # Używamy standardowego i bezpiecznego asyncio.run()
            message, status_code = asyncio.run(run_data_collection_cycle())
            logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
            return jsonify({"status": "success", "details": message}), status_code
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return app

if __name__ == '__main__':
    app = create_app()
    # Dodajemy ścieżkę, aby lokalne uruchomienie działało
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)