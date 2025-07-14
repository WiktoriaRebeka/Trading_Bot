# Lokalizacja: bot_service/app_setup.py

import sys
import traceback

print("DEBUG: bot_service/app_setup.py - Start pliku.", file=sys.stderr)

import logging
import uuid
from flask import Flask, jsonify

print("DEBUG: bot_service/app_setup.py - Podstawowe importy zakończone.", file=sys.stderr)

# Importy logiki biznesowej i inicjalizatorów
try:
    from shared_lib.config_loader import load_config
    print("DEBUG: bot_service/app_setup.py - zaimportowano load_config.", file=sys.stderr)
    from shared_lib.firebase_client import initialize_firebase
    print("DEBUG: bot_service/app_setup.py - zaimportowano initialize_firebase.", file=sys.stderr)
    from bot_service.bigquery_logger import initialize_bigquery
    print("DEBUG: bot_service/app_setup.py - zaimportowano initialize_bigquery.", file=sys.stderr)
    from bot_service.bot_logic import process_new_alerts, run_trading_logic
    print("DEBUG: bot_service/app_setup.py - zaimportowano bot_logic.", file=sys.stderr)
    from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp
    print("DEBUG: bot_service/app_setup.py - zaimportowano fetch_from_firestore.", file=sys.stderr)
except Exception as e:
    print(f"FATAL: Błąd podczas importów w app_setup.py: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    raise

logger = logging.getLogger(__name__)

def register_endpoints(app: Flask):
    #... (reszta funkcji bez zmian)
    @app.route('/')
    def health_check():
        return "Trading Bot Service is running.", 200

    @app.route('/health')
    def deep_health_check():
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            return jsonify({"status": "unhealthy", "reason": "Failed to initialize critical services."}), 503

    @app.route('/run-bot-cycle', methods=['POST'])
    def run_bot_cycle_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             logger.error("Zatrzymano cykl, ponieważ aplikacja nie została poprawnie zainicjalizowana.", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": "Service is unhealthy"}), 503
        try:
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            if new_alerts:
                logger.info(f"Przetwarzam {len(new_alerts)} nowych alertów.", extra={"json_fields": {"cycle_id": cycle_id}})
                process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            run_trading_logic()

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500


def initialize_app_services(app: Flask):
    """Wykonuje całą logikę inicjalizacji w kontekście aplikacji."""
    with app.app_context():
        # Używamy standardowego logowania, bo ustrukturyzowane mogło zawieść
        print("DEBUG: bot_service/app_setup.py - Wewnątrz initialize_app_services().", file=sys.stderr)
        
        try:
            import google.cloud.logging
            google.cloud.logging.Client().setup_logging()
            logging.info("Ustrukturyzowane logowanie Google Cloud (bot_service) skonfigurowane.")
        except Exception as e:
            logging.basicConfig(level=logging.INFO)
            print(f"WARNING: Logowanie GCP nie powiodło się, używam podstawowej konfiguracji: {e}", file=sys.stderr)

        
        print("DEBUG: bot_service/app_setup.py - Zamierzam wywołać load_config().", file=sys.stderr)
        load_config()
        print("DEBUG: bot_service/app_setup.py - load_config() wykonane.", file=sys.stderr)

        firebase_ok = initialize_firebase()
        print(f"DEBUG: bot_service/app_setup.py - initialize_firebase() wykonane, wynik: {firebase_ok}", file=sys.stderr)

        bigquery_ok = initialize_bigquery()
        print(f"DEBUG: bot_service/app_setup.py - initialize_bigquery() wykonane, wynik: {bigquery_ok}", file=sys.stderr)
        
        if firebase_ok and bigquery_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logging.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            logging.critical("Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503.")