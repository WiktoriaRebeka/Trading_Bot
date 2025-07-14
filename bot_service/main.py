# Lokalizacja: bot_service/main.py

import logging
import sys
import os
import uuid


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


from flask import Flask, jsonify
import google.cloud.logging

from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bot_logic import process_new_alerts, run_trading_logic
from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp


try:
    log_client = google.cloud.logging.Client()
    log_client.setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud zostało pomyślnie skonfigurowane.")
except Exception as e:

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.warning(f"Nie udało się skonfigurować logowania GCP, używam podstawowej konfiguracji. Błąd: {e}")


logger = logging.getLogger(__name__)


def create_app():
    """Tworzy i konfiguruje instancję aplikacji Flask (wzorzec Application Factory)."""
    app = Flask(__name__)
    app.config['INITIALIZATION_SUCCESS'] = False

    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji `bot_service`...")
        load_config()

        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()

        if firebase_ok and bigquery_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            logger.critical("Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503.")
    

    @app.route('/')
    def health_check():
        """Podstawowe sprawdzenie, czy serwis HTTP działa."""
        return "Trading Bot Service is running.", 200

    @app.route('/health')
    def deep_health_check():
        """Zaawansowane sprawdzenie, czy serwis jest w pełni gotowy do pracy."""
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            return jsonify({"status": "unhealthy", "reason": "Failed to initialize critical services."}), 503

    @app.route('/run-bot-cycle', methods=['POST']) # Zalecana metoda POST dla akcji zmieniających stan
    def run_bot_cycle_endpoint():
        # Użycie unikalnego ID dla każdego cyklu ułatwia śledzenie i debugowanie
        cycle_id = str(uuid.uuid4())
        
        # Logowanie z dodatkowymi, ustrukturyzowanymi danymi
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             logger.error("Zatrzymano cykl, ponieważ aplikacja nie została poprawnie zainicjalizowana.", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": "Service is unhealthy"}), 503
        try:
            # Krok 1: Przetwarzanie nowych alertów
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            if new_alerts:
                logger.info(f"Przetwarzam {len(new_alerts)} nowych alertów.", extra={"json_fields": {"cycle_id": cycle_id}})
                process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            # Krok 2: Uruchomienie głównej logiki tradingowej
            run_trading_logic()

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(
                f"Krytyczny błąd w głównym cyklu bota: {e}",
                exc_info=True, # Dołącza pełny traceback do logu
                extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}}
            )
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500
            
    return app


# Uruchomienie aplikacji
app = create_app()

if __name__ == '__main__':
    # Użycie zmiennej środowiskowej PORT jest wymagane przez Cloud Run
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False) # debug=False jest zalecane dla produkcji