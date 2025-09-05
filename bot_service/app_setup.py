# Lokalizacja: bot_service/app_setup.py

import logging
import uuid
from flask import jsonify, request

logger = logging.getLogger(__name__)

def initialize_app_services(app):
    """
    Inicjalizuje usługi zależne od aplikacji, takie jak połączenia z bazami danych.
    """
    logger.info("--- [APP_SETUP] Rozpoczynam initialize_app_services... ---")
    try:
        # Importy są wykonywane wewnątrz funkcji, aby uniknąć problemów przy starcie
        from shared_lib.firebase_client import initialize_firebase
        from bot_service.bigquery_logger import initialize_bigquery

        # Tutaj można dodać logikę ponawiania prób, jeśli jest potrzebna
        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()

        if firebase_ok and bigquery_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("--- [APP_SETUP] Inicjalizacja usług zakończona pomyślnie. ---")
        else:
            raise RuntimeError("Inicjalizacja jednej z usług (Firebase/BigQuery) nie powiodła się.")

    except Exception as e:
        app.config['INITIALIZATION_SUCCESS'] = False
        app.config['INITIALIZATION_FAILURE_REASON'] = str(e)
        logger.critical(f"--- [APP_SETUP] KRYTYCZNY BŁĄD podczas initialize_app_services: {e} ---", exc_info=True)
        # Rzucamy wyjątek dalej, aby zatrzymać tworzenie aplikacji, jeśli kluczowe usługi nie działają
        raise

def register_endpoints(app):
    """
    Rejestruje endpointy na przekazanym obiekcie `app`.
    """
    logger.info("--- [APP_SETUP] Rozpoczynam register_endpoints... ---")
    
    # Import logiki bota jest wykonywany "leniwie" tutaj, aby uniknąć cyklicznych zależności
    from bot_service.bot_logic import run_analysis_cycle

    @app.route('/')
    def health_check():
        return "Trading Bot Service is running.", 200

    @app.route('/health')
    def deep_health_check():
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
            return jsonify({"status": "unhealthy", "reason": reason}), 503

    @app.route('/run-bot-cycle', methods=['POST'])
    def run_bot_cycle_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- ROZPOCZĘCIE CYKLU BOTA --- ID cyklu: {cycle_id}")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
            logger.error(f"Zatrzymano cykl, aplikacja nie 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        try:
            run_analysis_cycle()
            logger.info(f"--- ZAKOŃCZENIE CYKLU BOTA --- ID cyklu: {cycle_id}")
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500