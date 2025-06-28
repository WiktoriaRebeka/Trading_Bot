# trading_bot/collector_main.py

from flask import Flask, jsonify
import logging
import sys

# Importy, które inicjują połączenie i logikę
from firebase_client import initialize_firebase
from data_collector import run_data_collection_cycle

# Podstawowa konfiguracja logowania
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [COLLECTOR] - %(message)s'
)
logger = logging.getLogger("collector.main")

# Inicjalizacja klienta Firestore przy starcie kontenera
firebase_initialized = initialize_firebase()
app = Flask(__name__)

@app.route('/')
def health_check():
    """Podstawowy endpoint sprawdzający, czy usługa działa."""
    return "Data Collector Service is running.", 200

@app.route('/run-collector-cycle', methods=['GET', 'POST'])
def run_collector_endpoint():
    """Główny endpoint, który będzie wywoływany przez Cloud Scheduler."""
    logger.info("--- ROZPOCZĘCIE CYKLU KOLEKTORA DANYCH (Cloud Run) ---")
    if not firebase_initialized:
        logger.critical("Firestore nie jest zainicjalizowane. Zatrzymuję cykl.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        message, status_code = run_data_collection_cycle(request=None)
        logger.info("--- ZAKOŃCZENIE CYKLU KOLEKTORA DANYCH ---")
        return jsonify({"status": "success", "details": message}), status_code

    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu kolektora: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500