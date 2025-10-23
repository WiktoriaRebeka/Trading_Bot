# Lokalizacja: bot_service/app_setup.py

import logging
import os
import uuid
from flask import Flask, jsonify, request

from shared_lib.secret_manager import get_secret
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bybit_executor import BybitExecutor
from bot_service.bot_logic import process_new_alerts, log_closed_positions_pnl, update_filled_orders, repair_old_orders


logger = logging.getLogger(__name__)

# --- Konfiguracja Aplikacji ---
GCP_PROJECT_ID = os.getenv("GCP_PROJECT")
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

def register_endpoints(app: Flask):
    @app.before_request
    def log_request_info():
        safe_headers = {str(k): str(v) for k, v in request.headers.items() if k.lower() not in ['authorization', 'cookie']}
        logger.info(
            f"--- OTRZYMANO ŻĄDANIE --- Endpoint: {request.path}, Metoda: {request.method}",
            extra={"json_fields": {"path": request.path, "method": request.method, "headers": safe_headers}}
        )

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

    @app.route('/process-alerts', methods=['POST'])
    def process_alerts_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- Rozpoczynam cykl przetwarzania alertów [ID: {cycle_id}] ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
             logger.error(f"Zatrzymano cykl, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        try:
            bybit_executor = app.config.get('BYBIT_EXECUTOR')
            if not bybit_executor:
                raise RuntimeError("BybitExecutor nie został poprawnie zainicjalizowany.")
            
            process_new_alerts(bybit_executor)

            logger.info(f"--- Cykl przetwarzania alertów zakończony pomyślnie [ID: {cycle_id}] ---")
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w cyklu przetwarzania alertów: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500


    @app.route('/log-pnl', methods=['POST'])
    def log_pnl_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- Rozpoczynam cykl logowania PnL [ID: {cycle_id}] ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
             logger.error(f"Zatrzymano cykl PnL, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503

        try:
            bybit_executor = app.config.get('BYBIT_EXECUTOR')
            if not bybit_executor:
                raise RuntimeError("BybitExecutor nie został poprawnie zainicjalizowany.")
            
            processed_count = log_closed_positions_pnl(bybit_executor)
            
            logger.info(f"--- Cykl logowania PnL zakończony. Przetworzono {processed_count} rekordów. [ID: {cycle_id}] ---")
            return jsonify({"status": "success", "processed_records": processed_count, "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w cyklu logowania PnL: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500
    
    @app.route('/update-orders', methods=['POST'])
    def update_orders_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- Rozpoczynam cykl aktualizacji zleceň [ID: {cycle_id}] ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
            logger.error(f"Zatrzymano cykl aktualizacji, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        try:
            bybit_executor = app.config.get('BYBIT_EXECUTOR')
            if not bybit_executor:
                raise RuntimeError("BybitExecutor nie został poprawnie zainicjalizowany.")
            
            update_filled_orders(bybit_executor)

            logger.info(f"--- Cykl aktualizacji zleceň zakończony pomyślnie [ID: {cycle_id}] ---")
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w cyklu aktualizacji zleceň: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500
    @app.route('/repair-old-orders', methods=['POST'])
    def repair_orders_endpoint():
        """Jednorazowy endpoint do naprawy starych zleceň bez statusu."""
        try:
            count = repair_old_orders()
            return jsonify({"status": "success", "repaired_count": count}), 200
        except Exception as e:
            logger.error(f"Błąd w skrypcie naprawczym: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

def initialize_app_services(app: Flask):
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji bot_service.")
        
        failure_reasons = []
        
        if not initialize_firebase(): failure_reasons.append("Failed to initialize Firebase/Firestore")
        if not initialize_bigquery(): failure_reasons.append("Failed to initialize BigQuery")
        
        if not GCP_PROJECT_ID:
            failure_reasons.append("Zmienna środowiskowa GCP_PROJECT nie jest ustawiona.")
        else:
            api_key = get_secret("bybit-api-key", GCP_PROJECT_ID)
            api_secret = get_secret("bybit-api-secret", GCP_PROJECT_ID)

            if api_key and api_secret:
                try:
                    executor = BybitExecutor(api_key=api_key, api_secret=api_secret, testnet=USE_TESTNET)
                    app.config['BYBIT_EXECUTOR'] = executor
                    logger.info(f"BybitExecutor pomyślnie zainicjalizowany. Tryb Testnet: {USE_TESTNET}")
                except Exception as e:
                    failure_reasons.append(f"Błąd inicjalizacji BybitExecutor: {e}")
            else:
                failure_reasons.append("Nie udało się pobrać kluczy API z Secret Manager.")

        if not failure_reasons:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            final_reason = " & ".join(failure_reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {final_reason}")