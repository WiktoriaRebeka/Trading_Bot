import os
import logging
import sys
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore

# --- Krok 1: Konfiguracja Logowania ---
# Ustawiamy podstawowy system logowania, aby widzieć, co się dzieje.
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Krok 2: Inicjalizacja Firebase (Teraz z Głośnym Błędem) ---
# Ten blok kodu jest teraz poza 'try...except'. Jeśli coś pójdzie nie tak,
# aplikacja się zatrzyma, a Render pokaże nam dokładny błąd.
logger.info("Rozpoczynam inicjalizację Firebase...")

# Sprawdzamy, czy zmienne środowiskowe są ustawione
cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
if not cred_path:
    logger.error("KRYTYCZNY BŁĄD: Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
    raise ValueError("Zmienna GOOGLE_APPLICATION_CREDENTIALS jest wymagana.")

project_id = os.environ.get("GCP_PROJECT_ID")
if not project_id:
    logger.error("KRYTYCZNY BŁĄD: Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona.")
    raise ValueError("Zmienna GCP_PROJECT_ID jest wymagana.")

# Inicjalizujemy połączenie z Firebase
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred, {
    'projectId': project_id
})

# Tworzymy klienta do poprawnej bazy danych 'trading-bot-data'
db = firestore.client(database="trading-bot-data")
logger.info(f"Inicjalizacja Firebase zakończona sukcesem. Połączono z bazą 'trading-bot-data' w projekcie '{project_id}'.")


# --- Krok 3: Definicja Aplikacji Flask ---
# Ten kod uruchomi się tylko, jeśli inicjalizacja Firebase w kroku 2 się powiedzie.
app = Flask(__name__)

@app.route("/")
def health_check():
    """Podstawowy endpoint sprawdzający, czy serwer żyje."""
    return "Webhook receiver is running and Firebase is connected.", 200

@app.route("/webhook", methods=['POST'])
def receive_alert():
    """Główny endpoint, który odbiera alerty z TradingView."""
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
        return jsonify({"status": "error", "message": "An internal error occurred."}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)