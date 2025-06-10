# TRADING_BOT/app/main.py
from flask import Flask, jsonify
import logging
import sys
import os # Dodajemy os do odczytu zmiennych środowiskowych

from .firebase_client import initialize_firebase, get_db
from . import constants
from . import state_manager
from . import fetch_from_firestore
from . import positions_logger 
from . import bot_logic 

# Konfiguracja logowania
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - [BOT_MAIN_D1] - %(message)s' # Nowy prefix
)
logger = logging.getLogger(__name__)

# --- INICJALIZACJA APLIKACJI I FIREBASE ---
logger.info("--- Ładowanie skryptu app/main.py (DEBUG D1) ---")
logger.info(f"GAE_ENV: {os.getenv('GAE_ENV')}") # Sprawdźmy, czy zmienne GAE są dostępne

firebase_initialized = False # Domyślnie false
try:
    logger.info("Wywołuję initialize_firebase()...")
    firebase_initialized = initialize_firebase() # Wywołujemy naszą scentralizowaną funkcję
    if firebase_initialized:
        logger.info("initialize_firebase() zwróciło True.")
        # Spróbujmy testowo pobrać klienta DB
        test_db = get_db()
        if test_db:
            logger.info("get_db() zwróciło klienta Firestore.")
        else:
            logger.error("get_db() zwróciło None!") # Nie powinno się zdarzyć, jeśli initialize_firebase zwróciło True
    else:
        logger.error("initialize_firebase() zwróciło False.")
        
except Exception as e_init:
    logger.error(f"Wyjątek podczas wywoływania initialize_firebase() lub get_db() w main.py: {e_init}", exc_info=True)
    firebase_initialized = False


# Tworzymy instancję Flask
app = Flask(__name__)
logger.info("Instancja Flask 'app' (DEBUG D1) utworzona.")

# --- ENDPOINTY FLASK ---
@app.route('/')
def health_check_d1():
    logger.info("Odebrano żądanie na / (health_check_d1)")
    if firebase_initialized:
        return "Trading Bot App (D1) is running! Firebase init OK.", 200
    else:
        return "Trading Bot App (D1) is running, but Firebase initialization FAILED.", 500

@app.route('/_ah/warmup')
def warmup_d1():
    logger.info("Obsługa żądania /_ah/warmup (D1)")
    return '', 200
    
logger.info("--- Zakończono ładowanie skryptu app/main.py (DEBUG D1) ---")