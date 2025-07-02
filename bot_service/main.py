#trading_bot/bot_service/main.py

import logging
import sys
import os

def create_app():
    """Tworzy i konfiguruje instancję aplikacji Flask dla głównego bota."""
    
    # Krok 1: Załaduj konfigurację. To powinno być pierwsze.
    from shared_lib.config_loader import load_config
    load_config()
    
    # Krok 2: Skonfiguruj logowanie.
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Krok 3: Stwórz instancję aplikacji Flask.
    from flask import Flask, jsonify
    app = Flask(__name__)

    # Krok 4: Zainicjuj WSZYSTKICH klientów usług w jednym, spójnym miejscu.
    from shared_lib.firebase_client import initialize_firebase
    initialize_firebase()
    
    from bot_service.bigquery_logger import initialize_bigquery
    initialize_bigquery()
    
    # Krok 5: Zaimportuj resztę logiki aplikacji.
    from datetime import datetime, timezone
    from pydantic import ValidationError
    from shared_lib.firebase_client import get_db
    from shared_lib import constants
    from bot_service import fetch_from_firestore
    from bot_service import bot_logic
    from shared_lib.models import AlertData, SetupData

    @app.route('/')
    def health_check():
        return "Trading Bot App is running.", 200

    @app.route('/run-bot-cycle', methods=['GET', 'POST'])
    def run_bot_cycle_endpoint():
        logger = logging.getLogger(__name__)
        from shared_lib.firebase_client import db_client as firebase_db_client
        
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")
        
        if not firebase_db_client:
            logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
            return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
            
        try:
            current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
            newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
            
            if newly_fetched_alerts:
                db = get_db()
                for alert_dict in newly_fetched_alerts:
                    try:
                        alert_data = AlertData.parse_obj(alert_dict)
                        if alert_data.direction_code == 1: 
                            alert_data.direction = "LONG"
                        elif alert_data.direction_code == -1: 
                            alert_data.direction = "SHORT"
                        else: 
                            continue
                        
                        new_setup = SetupData(alert_data=alert_data, updated_at=datetime.now(timezone.utc))
                        doc_ref = db.collection(constants.SETUP_COLLECTION).document(alert_data.symbol)
                        doc_ref.set(new_setup.dict(by_alias=True))
                        logger.info(f"[{alert_data.symbol}] Zarejestrowano/zaktualizowano aktywny setup.")
                        
                    except ValidationError as e:
                        logger.error(f"Błąd walidacji alertu. ID: {alert_dict.get('id')}. Błędy: {e}")
                
                if new_max_ts_dt and new_max_ts_dt > current_last_ts_dt:
                    fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)
                    
            bot_logic.run_trading_logic()
            
            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
            return jsonify({"status": "success"}), 200
            
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    return app

if __name__ == '__main__':
    app = create_app()
    # Dodanie ścieżki do sys.path dla uruchomienia lokalnego
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)