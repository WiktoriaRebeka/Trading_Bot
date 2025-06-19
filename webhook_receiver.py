# TRADING_BOT/webhook_receiver.py

import os
import logging
import sys
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore

# --- Konfiguracja Logowania ---
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO,
    format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Inicjalizacja Aplikacji Flask ---
app = Flask(__name__)

# --- Inicjalizacja Firebase ---
db = None # Ustawiamy domyślnie na None
try:
    # Render.com udostępnia klucz jako plik w ścieżce zdefiniowanej w GOOGLE_APPLICATION_CREDENTIALS
    cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not cred_path:
        raise ValueError("Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
        
    cred = credentials.Certificate(cred_path)
    
    # Przekazujemy ID bazy danych podczas inicjalizacji całej aplikacji
    # Jest to wymagane, jeśli nie używamy bazy '(default)'
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise ValueError("Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona.")

    firebase_admin.initialize_app(cred, {
        'projectId': project_id
    })
    
    # Teraz tworzymy klienta, wskazując na konkretną bazę danych
    db = firestore.client(database="trading-bot-data")
    logger.info("Inicjalizacja Firebase dla webhooka zakończona sukcesem. Połączono z bazą 'trading-bot-data'.")

except Exception as e:
    logger.error(f"KRYTYCZNY BŁĄD inicjalizacji Firebase: {e}", exc_info=True)
    # db pozostaje None, więc endpointy zwrócą błąd

@app.route("/")
def health_check():
    """Podstawowy health check, żeby zobaczyć czy serwer działa."""
    status = "OK" if db else "INITIALIZATION FAILED"
    return f"Webhook receiver is running. Firebase status: {status}", 200

@app.route("/webhook", methods=['POST'])
def receive_alert():
    """Główny endpoint, który odbiera alerty z TradingView."""
    if not db:
        logger.error("Baza danych Firestore nie jest dostępna. Pomijam alert.")
        return jsonify({"status": "error", "message": "Database not initialized"}), 500

    try:
        alert_data = request.json
        if not alert_data:
            logger.warning("Otrzymano puste żądanie.")
            return jsonify({"status": "error", "message": "Empty request"}), 400

        logger.info(f"Otrzymano alert: {alert_data}")

        # Dodajemy serwerowy timestamp, kiedy alert został odebrany
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP

        # Zapisujemy alert do kolekcji 'alerts'
        doc_ref = db.collection('alerts').document()
        doc_ref.set(alert_data)

        logger.info(f"Pomyślnie zapisano alert do Firestore. ID dokumentu: {doc_ref.id}")
        return jsonify({"status": "success", "doc_id": doc_ref.id}), 201

    except Exception as e:
        logger.error(f"Błąd podczas przetwarzania alertu: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)