# /trading_bot/main.py (Wersja dla Architektury Stanu Ciągłego v5.1)

from flask import Flask, jsonify
import logging
import sys
import os
from datetime import datetime, timezone

# --- Importy modułów aplikacji ---
from firebase_client import initialize_firebase, get_db
import constants
import fetch_from_firestore
import bot_logic

# --- Konfiguracja Logowania ---
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("app.main") 

# --- Inicjalizacja Aplikacji i Firebase ---
firebase_initialized = initialize_firebase()

app = Flask(__name__)

@app.route('/')
def health_check():
    status_message = "Firebase OK" if firebase_initialized else "Firebase FAILED"
    gae_version = os.getenv('GAE_VERSION', 'N/A')
    return f"Trading Bot App (Wersja: {gae_version}) is running! {status_message}.", 200

@app.route('/_ah/warmup')
def warmup():
    logger.info("Obsługa żądania /_ah/warmup")
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA (Architektura Stanu Ciągłego) ---")

    if not firebase_initialized:
        logger.error("Błąd krytyczny: Firebase nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # 1. Pobierz nowe alerty z kolejki `alerts`
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        
        # 2. Przetwórz nowe alerty, NADPISUJĄC setup dla danego symbolu
        if newly_fetched_alerts:
            db = get_db()
            for alert_data in newly_fetched_alerts:
                if alert_data.get('type') == 'OrderBlock':
                    # Logika do obsługi 'direction' pozostaje
                    direction_code = alert_data.get('directionCode')
                    if direction_code == 1: alert_data['direction'] = "LONG"
                    elif direction_code == -1: alert_data['direction'] = "SHORT"
                    else:
                        logger.warning(f"Otrzymano alert z nieprawidłowym directionCode: {direction_code}")
                        continue
                    
                    symbol = alert_data.get('symbol')
                    if not symbol:
                        logger.warning(f"Otrzymano alert bez symbolu: {alert_data}")
                        continue

                    # Nowy alert zawsze nadpisuje stary setup dla tego symbolu.
                    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
                    
                    # Tworzymy kompletny, "świeży" stan dla tego symbolu.
                    # To resetuje wszystkie liczniki i statusy dla nowego OB.
                    new_symbol_state = {
                        "alert_data": alert_data,
                        "is_position_open": False,
                        "entry_attempts": 0,
                        "last_known_price": None,
                        "active_trade_id": None,
                        "active_trade_entry_price": None,
                        "active_trade_entry_timestamp_ms": None,
                        "updated_at": datetime.now(timezone.utc)
                    }
                    doc_ref.set(new_symbol_state)
                    logger.info(f"[{symbol}] Zarejestrowano/zaktualizowano AKTYWNY SETUP (stan zresetowany).")
            
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)

        # 3. Pobierz wszystkie ceny rynkowe
        all_current_prices = bot_logic.get_all_prices_for_category()
        if not all_current_prices:
            logger.warning("Nie udało się pobrać cen rynkowych. Pomijam cykl logiki.")
            return jsonify({"status": "warning", "message": "Failed to fetch prices"}), 200
        
        # 4. Uruchom główną pętlę logiki
        bot_logic.run_trading_logic(all_current_prices)

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd w cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
