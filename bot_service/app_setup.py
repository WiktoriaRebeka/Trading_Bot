# Lokalizacja: bot_service/app_setup.py
import logging
import uuid
from flask import Flask, jsonify

# Importy z bibliotek współdzielonych
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase

# Importy z bieżącego serwisu (bot_service)
from bot_service.bigquery_logger import initialize_bigquery  # <-- KLUCZOWA POPRAWKA
from bot_service.bot_logic import initialize_trading_services, process_new_alerts, run_trading_logic
from bot_service.fetch_from_firestore import fetch_new_alerts_since, load_last_processed_timestamp, save_last_processed_timestamp

logger = logging.getLogger(__name__)

def register_endpoints(app: Flask):
    """Rejestruje wszystkie endpointy aplikacji."""
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
        logger.info("Rozpoczynam konfigurację aplikacji `bot_service` wewnątrz kontekstu.")
        
        # Krok 1: Załaduj konfigurację i sekrety. To musi być pierwsze.
        load_config()

        # Krok 2: Inicjalizuj poszczególne usługi i śledź ich status.
        firebase_ok = initialize_firebase()
        if not firebase_ok:
            app.config['INITIALIZATION_FAILURE_REASON'] = "Failed to initialize Firebase/Firestore."
            logger.critical(app.config['INITIALIZATION_FAILURE_REASON'])
        
        bigquery_ok = initialize_bigquery()
        if not bigquery_ok:
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', '')
            new_reason = "Failed to initialize BigQuery."
            app.config['INITIALIZATION_FAILURE_REASON'] = f"{reason} {new_reason}".strip()
            logger.critical(new_reason)

        # Krok 3: Inicjalizuj usługi tradingowe, które zależą od załadowanej konfiguracji.
        trading_ok = initialize_trading_services()
        if not trading_ok:
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', '')
            new_reason = "Failed to initialize BybitExecutor."
            app.config['INITIALIZATION_FAILURE_REASON'] = f"{reason} {new_reason}".strip()
            logger.critical(new_reason)

        # Krok 4: Sprawdź, czy WSZYSTKIE kluczowe usługi zostały zainicjalizowane poprawnie.
        if firebase_ok and bigquery_ok and trading_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            # Logujemy ostateczny, skumulowany powód błędu.
            final_reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {final_reason}")