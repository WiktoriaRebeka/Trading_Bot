# Lokalizacja: collector_service/app_setup.py (NOWY PLIK)

import logging
import uuid
import asyncio
from flask import Flask, jsonify, request

from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from collector_service.data_collector import run_data_collection_cycle

logger = logging.getLogger(__name__)

def register_endpoints(app: Flask):
    @app.before_request
    def log_request_info():
        headers = {k: v for k, v in request.headers if k.lower() not in ['authorization', 'cookie']}
        logger.info(
            f"--- OTRZYMANO ŻĄDANIE --- Endpoint: {request.path}, Metoda: {request.method}",
            extra={"json_fields": {"path": request.path, "method": request.method, "headers": headers}}
        )

    @app.route('/')
    def health_check():
        return "Data Collector Service is running.", 200
        
    @app.route('/health')
    def deep_health_check():
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
            return jsonify({"status": "unhealthy", "reason": reason}), 503

    @app.route('/run-collector-cycle', methods=['POST'])
    def run_collector_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
            logger.critical(f"Firestore nie jest zainicjalizowane. Zatrzymuję cykl. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        try:
            message, status_code = asyncio.run(run_data_collection_cycle(cycle_id))
            logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "details": message, "cycle_id": cycle_id}), status_code
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

def initialize_app_services(app: Flask):
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji collector_service wewnątrz kontekstu.")
        
        if initialize_firebase():
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [collector] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            reason = "Failed to initialize Firebase/Firestore"
            app.config['INITIALIZATION_FAILURE_REASON'] = reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {reason}")