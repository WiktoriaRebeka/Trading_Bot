import os
import logging
import sys
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore

# --- Krok 1: Konfiguracja Logowania ---
# Solidne logowanie, aby wszystko było widoczne
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Krok 2: Kanoniczna Inicjalizacja Firebase ---
# Ten blok kodu jest teraz kluczowy. Jeśli coś tu zawiedzie, aplikacja nie wystartuje.

try:
    logger.info("Rozpoczynam inicjalizację Firebase Admin SDK...")

    # Sprawdzenie, czy zmienne środowiskowe są dostępne
    cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    project_id = os.environ.get("GCP_PROJECT_ID")
    database_name = "trading-bot-data" # Twoja nazwana baza danych

    if not cred_path:
        raise ValueError("KRYTYCZNY BŁĄD: Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
    if not project_id:
        raise ValueError("KRYTYCZNY BŁĄD: Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona.")

    logger.info(f"Ścieżka do credentials: {cred_path}")
    logger.info(f"ID Projektu: {project_id}")
    logger.info(f"Docelowa baza danych: {database_name}")

    # Inicjalizacja aplikacji Firebase
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'projectId': project_id
    })

    # NAJWAŻNIEJSZE: Tworzymy klienta do KONKRETNEJ, NAZWANEJ bazy danych
    db = firestore.client(database=database_name)
    
    # Testowe zapytanie, aby potwierdzić połączenie (opcjonalne, ale bardzo przydatne)
    # To zapytanie nie musi nic znaleźć, ważne że nie zwróci błędu.
    db.collection('__test_connection__').limit(1).get()

    logger.info(f"Inicjalizacja Firebase zakończona. Pomyślnie połączono z bazą '{database_name}'.")

except Exception as e:
    logger.error(f"FATALNY BŁĄD PODCZAS INICJALIZACJI FIREBASE: {e}", exc_info=True)
    # Zatrzymujemy aplikację, jeśli nie udało się połączyć z bazą
    raise

# --- Krok 3: Aplikacja Flask ---
app = Flask(__name__)

@app.route("/")
def health_check():
    """Podstawowy endpoint sprawdzający, czy serwer żyje."""
    return "Webhook receiver is active and connected to Firestore.", 200

@app.route("/webhook", methods=['POST'])
def receive_alert():
    """Główny endpoint, który odbiera alerty z TradingView."""
    try:
        alert_data = request.json
        if not alert_data:
            logger.warning("Otrzymano puste żądanie (brak ciała JSON).")
            return jsonify({"status": "error", "message": "Empty JSON body"}), 400

        logger.info(f"Odebrano alert: {alert_data}")

        # Dodajemy serwerowy timestamp, aby wiedzieć, kiedy go otrzymaliśmy
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP

        # Zapisujemy alert do kolekcji 'alerts'
        doc_ref = db.collection('alerts').document()
        doc_ref.set(alert_data)

        logger.info(f"Pomyślnie zapisano alert w Firestore. ID dokumentu: {doc_ref.id}")
        return jsonify({"status": "success", "doc_id": doc_ref.id}), 201

    except Exception as e:
        logger.error(f"Błąd podczas przetwarzania alertu: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "An internal server error occurred."}), 500

if __name__ == '__main__':
    # Render.com ustawia port dynamicznie przez zmienną środowiskową PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)