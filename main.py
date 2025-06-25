# /trading_bot/main.py (WERSJA FINALNA - Architektura Dwustanowa)

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
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA (Architektura Dwustanowa) ---")

    if not firebase_initialized:
        logger.error("Błąd krytyczny: Firebase nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # === ETAP 1: ZARZĄDZANIE NOWYMI SETUPAMI ===
        # Pobierz nowe alerty z kolejki `alerts`
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        
        # Przetwórz nowe alerty, NADPISUJĄC setup w kolekcji `active_setups`
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

                    # Zapisujemy/nadpisujemy stan w dedykowanej kolekcji dla setupów.
                    # To unieważnia stary OB dla NOWYCH wejść.
                    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
                    
                    new_setup_state = {
                        "alert_data": alert_data,
                        "entry_attempts": 0,
                        "updated_at": datetime.now(timezone.utc)
                    }
                    doc_ref.set(new_setup_state)
                    logger.info(f"[{symbol}] Zarejestrowano/zaktualizowano aktywny setup w '{constants.SETUP_COLLECTION}'.")
            
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)

        # === ETAP 2: EGZEKUCJA LOGIKI TRADINGOWEJ ===
        # Pobierz wszystkie ceny rynkowe
        all_current_prices = bot_logic.get_all_prices_for_category()
        if not all_current_prices:
            logger.warning("Nie udało się pobrać cen rynkowych. Pomijam cykl logiki.")
            return jsonify({"status": "warning", "message": "Failed to fetch prices"}), 200
        
        # Uruchom główną logikę, która zajmie się zarówno otwieraniem nowych
        # pozycji (na podstawie `active_setups`), jak i monitorowaniem już
        # otwartych (z `open_trades`).
        bot_logic.run_trading_logic(all_current_prices)

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500