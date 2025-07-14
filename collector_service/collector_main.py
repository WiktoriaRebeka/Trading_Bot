# Lokalizacja: collector_service/collector_main.py

import logging
import sys
import os
import asyncio
import uuid

# Krok 1: Wstawienie ścieżki.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Krok 2: Importy podstawowe.
from flask import Flask, jsonify
import google.cloud.logging

# Krok 3: Konfiguracja logowania.
try:
    log_client = google.cloud.logging.Client()
    log_client.setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud (collector) zostało skonfigurowane.")
except Exception as e:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - [COLLECTOR] - %(name)s - %(levelname)s - %(message)s')
    logging.warning(f"Nie udało się skonfigurować logowania GCP (collector), używam podstawowej konfiguracji. Błąd: {e}")

logger = logging.getLogger(__name__)

def create_app():
    """Tworzy i konfiguruje instancję aplikacji Flask."""
    app = Flask(__name__)
    app.config['INITIALIZATION_SUCCESS'] = False

    # Importy inicjalizacyjne
    from shared_lib.config_loader import load_config
    from shared_lib.firebase_client import initialize_firebase

    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji `collector_service` wewnątrz kontekstu.")
        load_config()
        if initialize_firebase():
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [collector] została pomyślnie utworzona i skonfigurowana.")
        else:
            logger.critical("Krytyczny błąd podczas inicjalizacji Firebase w kolektorze.")
    
    # Rejestracja endpointów
    @app.route('/')
    def health_check():
        return "Data Collector Service is running.", 200
        
    @app.route('/health')
    def deep_health_check():
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            return jsonify({"status": "unhealthy", "reason": "Failed to initialize Firebase"}), 503

    @app.route('/run-collector-cycle', methods=['POST'])
    def run_collector_endpoint():
        # Leniwy import logiki
        from collector_service.data_collector import run_data_collection_cycle

        cycle_id = str(uuid.uuid4())
        logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": "Service is unhealthy"}), 503
        
        try:
            message, status_code = asyncio.run(run_data_collection_cycle(cycle_id))
            logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "details": message, "cycle_id": cycle_id}), status_code
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500
            
    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)