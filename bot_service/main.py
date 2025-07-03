# Lokalizacja: bot_service/main.py

import logging
import sys
import os

def create_app():
    from flask import Flask, jsonify
    from shared_lib.config_loader import load_config
    from shared_lib.firebase_client import initialize_firebase
    from bot_service.bigquery_logger import initialize_bigquery
    from bot_service.bot_logic import process_new_alerts, run_trading_logic
    from bot_service.fetch_from_firestore import load_last_processed_timestamp, fetch_new_alerts_since, save_last_processed_timestamp

    load_config()
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    initialize_firebase()
    initialize_bigquery()
    
    app = Flask(__name__)
    logging.info("Aplikacja Flask [bot_service] została utworzona.")

    @app.route('/')
    def health_check():
        return "Trading Bot Service is running.", 200

    @app.route('/run-bot-cycle', methods=['GET', 'POST'])
    def run_bot_cycle_endpoint():
        logger = logging.getLogger(__name__)
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")
        
        try:
            # 1. Pobierz dane o alertach
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            
            # 2. Przetwórz nowe alerty i utwórz/zaktualizuj setupy
            if new_alerts:
                process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            # 3. Uruchom główną logikę tradingową
            run_trading_logic()
            
            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
            return jsonify({"status": "success"}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return app

if __name__ == '__main__':
    app = create_app()
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)