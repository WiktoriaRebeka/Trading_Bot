# Lokalizacja: collector_service/collector_main.py

import logging
import sys
import asyncio
import os
from flask import Flask, jsonify

# --- POPRAWIONE IMPORTY ---
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase, db_client
from collector_service.data_collector import run_data_collection_cycle

# Uruchamiamy konfigurację i inicjalizację na poziomie modułu,
# aby błędy pojawiły się jak najwcześniej.
print("COLLECTOR: Ładowanie konfiguracji...")
load_config()
print("COLLECTOR: Konfiguracja załadowana.")

print("COLLECTOR: Inicjalizacja Firebase...")
initialize_firebase()
print("COLLECTOR: Firebase zainicjalizowane.")

# Tworzymy aplikację Flask globalnie
app = Flask(__name__)

# Konfigurujemy logowanie
logging.basicConfig(
    stream=sys.stdout, level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [COLLECTOR] - %(message)s'
)
# Testowe logowanie, aby sprawdzić, czy ten punkt jest osiągany
logging.info("Aplikacja Flask [collector] została utworzona.")

@app.route('/')
def health_check():
    return "Data Collector Service is running.", 200

@app.route('/run-collector-cycle', methods=['GET', 'POST'])
def run_collector_endpoint():
    logger = logging.getLogger(__name__)
    logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH ---")
    if not db_client:
        logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
    try:
        # Używamy prostszego sposobu na uruchomienie funkcji asynchronicznej
        message, status_code = asyncio.run(run_data_collection_cycle())
        logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
        return jsonify({"status": "success", "details": message}), status_code
    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)