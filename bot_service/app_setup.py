# Lokalizacja: bot_service/app_setup.py

import logging
import uuid
import time
from flask import Flask, jsonify, request

# Importy logiki biznesowej i inicjalizatorów
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bot_logic import process_new_alerts, run_analysis_cycle
from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

logger = logging.getLogger(__name__)

MAX_INIT_RETRIES = 3
INIT_RETRY_DELAY_SECONDS = 5

def register_endpoints(app: Flask):
    """Rejestruje wszystkie endpointy aplikacji."""

    @app.before_request
    def log_request_info():
        """Loguje informacje o każdym przychodzącym żądaniu."""
        headers = {k: v for k, v in request.headers if k.lower() not in ['authorization', 'cookie']}
        logger.info(
            f"--- OTRZYMANO ŻĄDANIE --- Endpoint: {request.path}, Metoda: {request.method}, IP: {request.remote_addr}",
            extra={"json_fields": {"path": request.path, "method": request.method, "ip": request.remote_addr, "headers": headers}}
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

    @app.route('/run-bot-cycle', methods=['POST'])
    def run_bot_cycle_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
             logger.error(f"Zatrzymano cykl, ponieważ aplikacja nie została poprawnie zainicjalizowana. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        try:
            logger.info(f"[CYKL {cycle_id}] Krok 1: Ładowanie ostatniego timestampu.")
            last_ts = load_last_processed_timestamp()
            
            logger.info(f"[CYKL {cycle_id}] Krok 2: Pobieranie nowych alertów od {last_ts.isoformat()}.")
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            
            if new_alerts:
                logger.info(f"[CYKL {cycle_id}] Krok 3: Przetwarzanie {len(new_alerts)} nowych alertów.")
                process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    logger.info(f"[CYKL {cycle_id}] Krok 4: Zapisywanie nowego timestampu {new_ts.isoformat()}.")
                    save_last_processed_timestamp(new_ts)
            else:
                logger.info(f"[CYKL {cycle_id}] Krok 3 i 4 pominięte - brak nowych alertów.")

            logger.info(f"[CYKL {cycle_id}] Krok 5: Uruchamianie głównej pętli analitycznej.")
            run_analysis_cycle()

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

def initialize_app_services(app: Flask):
    """Wykonuje całą logikę inicjalizacji w kontekście aplikacji."""
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji bot_service wewnątrz kontekstu.")
        
        failure_reasons = []

        # Używamy uproszczonego load_config, który nie łączy się z Secret Manager API
        load_config()

        firebase_ok = False
        for attempt in range(1, MAX_INIT_RETRIES + 1):
            if initialize_firebase():
                firebase_ok = True
                break
            if attempt < MAX_INIT_RETRIES:
                time.sleep(INIT_RETRY_DELAY_SECONDS)
        if not firebase_ok:
            failure_reasons.append("Failed to initialize Firebase/Firestore")
        
        bigquery_ok = False
        for attempt in range(1, MAX_INIT_RETRIES + 1):
            if initialize_bigquery():
                bigquery_ok = True
                break
            if attempt < MAX_INIT_RETRIES:
                time.sleep(INIT_RETRY_DELAY_SECONDS)
        if not bigquery_ok:
            failure_reasons.append("Failed to initialize BigQuery")
        
        if not failure_reasons:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            final_reason = " & ".join(failure_reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {final_reason}")