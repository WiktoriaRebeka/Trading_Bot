# W pliku: /collector_main.py
# ZASTĄP CAŁĄ ZAWARTOŚĆ PLIKU

import logging
import sys
import asyncio

# Krok 1: Załaduj konfigurację PRZED WSZYSTKIM INNYM.
from config_loader import load_config
load_config()

# Krok 2: Importuj resztę modułów
from flask import Flask, jsonify
from firebase_client import initialize_firebase
from data_collector import run_data_collection_cycle

# Konfiguracja logowania
logging.basicConfig(
    stream=sys.stdout, level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [COLLECTOR] - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicjalizacja aplikacji
firebase_initialized = initialize_firebase()
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Data Collector Service is running.", 200

@app.route('/run-collector-cycle', methods=['GET', 'POST'])
def run_collector_endpoint():
    """Główny endpoint, który będzie wywoływany przez Cloud Scheduler."""
    if not firebase_initialized:
        logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
    try:
        # Uruchamiamy główną funkcję asynchroniczną za pomocą asyncio.run()
        message, status_code = asyncio.run(run_data_collection_cycle())
        logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
        return jsonify({"status": "success", "details": message}), status_code
    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500