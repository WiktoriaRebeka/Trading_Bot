# Lokalizacja: bot_service/main.py

import logging
import sys
import os

# Konfigurujemy logowanie na samym początku, aby złapać wszystkie błędy
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Dodajemy ścieżkę, to jest wciąż dobra praktyka
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- KLUCZOWA ZMIANA DIAGNOSTYCZNA ---
# Otaczamy tworzenie aplikacji blokiem try...except, aby złapać ukryty błąd
try:
    from flask import Flask, jsonify
    from shared_lib.config_loader import load_config
    from shared_lib.firebase_client import initialize_firebase
    from bot_service.bigquery_logger import initialize_bigquery
    from bot_service.bot_logic import process_new_alerts, run_trading_logic
    from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

    # Ta funkcja jest teraz zdefiniowana wewnątrz bloku try
    def create_app():
        app = Flask(__name__)

        with app.app_context():
            # Przenosimy konfigurację logowania do góry, ale zostawiamy resztę tutaj
            load_config()

            if not initialize_firebase():
                logger.critical("Krytyczny błąd: Inicjalizacja Firebase nie powiodła się.")
            
            if not initialize_bigquery():
                logger.critical("Krytyczny błąd: Inicjalizacja BigQuery nie powiodła się.")

        logger.info("Aplikacja Flask [bot_service] została utworzona i skonfigurowana.")

        @app.route('/')
        def health_check():
            return "Trading Bot Service is running.", 200

        @app.route('/run-bot-cycle', methods=['GET', 'POST'])
        def run_bot_cycle_endpoint():
            cycle_logger = logging.getLogger('BotCycle')
            cycle_logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")
            try:
                last_ts = load_last_processed_timestamp()
                new_alerts, new_ts = fetch_new_alerts_since(last_ts)
                if new_alerts:
                    process_new_alerts(new_alerts)
                    if new_ts and new_ts > last_ts:
                        save_last_processed_timestamp(new_ts)
                run_trading_logic()
                cycle_logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
                return jsonify({"status": "success"}), 200
            except Exception as e:
                cycle_logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
                return jsonify({"status": "error", "message": str(e)}), 500
                
        return app

    # Jeśli wszystko poszło dobrze, tworzymy instancję aplikacji
    app = create_app()

except Exception as e:
    # Jeśli wystąpi jakikolwiek błąd podczas importu lub definiowania funkcji,
    # zalogujemy go tutaj. To jest nasz cel.
    logger.critical(f"KRYTYCZNY BŁĄD PODCZAS INICJALIZACJI APLIKACJI: {e}", exc_info=True)
    # Zatrzymujemy proces, aby błąd był widoczny
    sys.exit(1)


if __name__ == '__main__':
    # Ta część jest tylko do lokalnego uruchamiania, nie ma wpływu na Cloud Run
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
