# Lokalizacja: bot_service/app_setup.py

import logging
import uuid
import time
from flask import Flask, jsonify, request

from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bot_logic import process_new_alerts, run_analysis_cycle
from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

logger = logging.getLogger(__name__)

MAX_INIT_RETRIES = 3
INIT_RETRY_DELAY_SECONDS = 5

def register_endpoints(app: Flask):
    @app.before_request
    def log_request_info():
        safe_headers = {}
        try:
            for key, value in request.headers.items():
                if key.lower() not in ['authorization', 'cookie']:
                    safe_headers[str(key)] = str(value)
        except Exception as e:
            logger.warning(f"Nie udało się w pełni sparsować nagłówków żądania: {e}")

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

    @app.route('/run-bot-cycle', methods=['POST'])
    def run_bot_cycle_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- [DEBUG] KROK 1: Wejście do endpointu /run-bot-cycle. ID cyklu: {cycle_id} ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
             logger.error(f"[DEBUG] Zatrzymano cykl, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        logger.info(f"[DEBUG] KROK 2: Aplikacja jest 'healthy'. Rozpoczynam blok try...except.")
        try:
            logger.info(f"[DEBUG] KROK 3: Wywołuję load_last_processed_timestamp().")
            last_ts = load_last_processed_timestamp()
            
            logger.info(f"[DEBUG] KROK 4: Wywołuję fetch_new_alerts_since() z timestampem: {last_ts.isoformat() if last_ts else 'None'}.")
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            
            logger.info(f"[DEBUG] KROK 5: Otrzymano {len(new_alerts)} nowych alertów.")
            if new_alerts:
                logger.info(f"[DEBUG] KROK 6: Warunek 'if new_alerts' jest prawdziwy. Wywołuję process_new_alerts().")
                process_new_alerts(new_alerts)
                
                if new_ts and (not last_ts or new_ts > last_ts):
                    logger.info(f"[DEBUG] KROK 7: Zapisuję nowy timestamp: {new_ts.isoformat()}.")
                    save_last_processed_timestamp(new_ts)
                else:
                    logger.warning(f"[DEBUG] KROK 7: Pomijam zapis timestampa (new_ts: {new_ts}, last_ts: {last_ts}).")
            else:
                logger.info(f"[DEBUG] KROK 6: Warunek 'if new_alerts' jest fałszywy. Pomijam przetwarzanie.")

            logger.info(f"[DEBUG] KROK 8: Wywołuję run_analysis_cycle().")
            run_analysis_cycle()

            logger.info(f"--- [DEBUG] KROK 9: Cykl zakończony pomyślnie. Zwracam HTTP 200. ID cyklu: {cycle_id} ---")
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"[DEBUG] KRYTYCZNY BŁĄD w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

def initialize_app_services(app: Flask):
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji bot_service wewnątrz kontekstu.")
        
        failure_reasons = []
        
        firebase_ok = False
        for attempt in range(1, MAX_INIT_RETRIES + 1):
            if initialize_firebase():
                firebase_ok = True
                break
            logger.warning(f"Inicjalizacja Firebase nie powiodła się (próba {attempt}/{MAX_INIT_RETRIES}).")
            if attempt < MAX_INIT_RETRIES:
                time.sleep(INIT_RETRY_DELAY_SECONDS)
        if not firebase_ok:
            failure_reasons.append("Failed to initialize Firebase/Firestore")
        
        bigquery_ok = False
        for attempt in range(1, MAX_INIT_RETRIES + 1):
            if initialize_bigquery():
                bigquery_ok = True
                break
            logger.warning(f"Inicjalizacja BigQuery nie powiodła się (próba {attempt}/{MAX_INIT_RETRIES}).")
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