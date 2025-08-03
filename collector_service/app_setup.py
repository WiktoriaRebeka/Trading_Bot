# Lokalizacja: collector_service/app_setup.py 

import logging
import uuid
import asyncio
from flask import Flask, jsonify

# Importy
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from collector_service.data_collector import run_data_collection_cycle

logger = logging.getLogger(__name__)

def register_endpoints(app: Flask):
    """Rejestruje endpointy dla kolektora."""

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

def initialize_app_services(app: Flask):
    """Inicjalizuje usługi dla kolektora."""
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji `collector_service` wewnątrz kontekstu.")
        load_config()
        if initialize_firebase():
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [collector] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            logger.critical("Krytyczny błąd podczas inicjalizacji Firebase w kolektorze.")