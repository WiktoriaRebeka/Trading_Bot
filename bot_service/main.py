# Lokalizacja: bot_service/main.py

import logging
import sys
import os
import uuid

# Krok 1: Wstawienie ścieżki na samym początku. To jest OK.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Krok 2: Importy. Przenosimy je, aby były dostępne dla funkcji.
from flask import Flask, jsonify
import google.cloud.logging

# UWAGA: Te importy pozostają tutaj, ponieważ są potrzebne wewnątrz funkcji create_app.
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bot_logic import process_new_alerts, run_trading_logic
from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

# Krok 3: Konfiguracja logowania. To JEDYNA rzecz, która powinna dziać się na poziomie globalnym.
# Robimy to raz, na samym początku.
try:
    log_client = google.cloud.logging.Client()
    log_client.setup_logging()
    logging.info("Ustrukturyzowane logowanie Google Cloud zostało pomyślnie skonfigurowane.")
except Exception as e:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.warning(f"Nie udało się skonfigurować logowania GCP, używam podstawowej konfiguracji. Błąd: {e}")

# Utworzenie głównego loggera dla aplikacji. To również jest OK na poziomie globalnym.
logger = logging.getLogger(__name__)

def create_app():
    """Tworzy i konfiguruje instancję aplikacji Flask (wzorzec Application Factory)."""
    app = Flask(__name__)
    app.config['INITIALIZATION_SUCCESS'] = False

    # CAŁA LOGIKA INICJALIZACJI MUSI BYĆ W KONTEKŚCIE APLIKACJI
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji `bot_service` wewnątrz kontekstu.")
        load_config()

        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()

        if firebase_ok and bigquery_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            logger.critical("Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503.")
    
    # Rejestracja endpointów
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
            logger.error(
                f"Krytyczny błąd w głównym cyklu bota: {e}",
                exc_info=True,
                extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}}
            )
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500
            
    return app

app = create_app()

if __name__ == '__main__':
    # Ta sekcja jest używana tylko przy uruchamianiu lokalnym (np. `python main.py`)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)