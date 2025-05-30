# trading_bot/app/webhook.py
from flask import Flask, request, jsonify
from datetime import datetime, timezone # Użyj timezone
import os
import json
import requests
# Usunięto dotenv, bo constants.py to robi, a tu nie jest bezpośrednio potrzebny
# Chyba że webhook jest uruchamiany zupełnie niezależnie.
# Dla bezpieczeństwa można zostawić:
# from dotenv import load_dotenv
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Użyj stałych, jeśli to możliwe, ale webhook może być deployowany inaczej
# Dla uproszczenia, zostawiam bezpośrednie odwołania do os.getenv
# Jeśli constants.py jest dostępne w środowisku Render, można importować

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://cceowvpxutqxnaypnopr.supabase.co")
SUPABASE_API_KEY = os.getenv("SUPABASE_ANON_KEY")
TABLE_NAME = os.getenv("SUPABASE_TABLE_NAME", "alerts")

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    if not SUPABASE_API_KEY:
        print("[WEBHOOK_ERROR] Brak klucza API Supabase.")
        return jsonify({"error": "Server configuration error"}), 500
    try:
        data = request.get_json(force=True) # force=True może maskować błędy klienta
    except Exception as e:
        print(f"[WEBHOOK_ERROR] Nieprawidłowy JSON: {e}")
        return jsonify({"error": "Invalid JSON payload"}), 400

    if not isinstance(data, dict):
        print(f"[WEBHOOK_ERROR] Otrzymane dane nie są słownikiem: {type(data)}")
        return jsonify({"error": "Payload must be a JSON object"}), 400

    # Dodaj znacznik czasu UTC
    data["received_at"] = datetime.now(timezone.utc).isoformat()

    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal" # Nie potrzebujemy pełnej odpowiedzi
    }

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}",
            headers=headers,
            data=json.dumps(data), # Upewnij się, że wysyłasz string JSON
            timeout=10
        )
        response.raise_for_status() # Rzuci wyjątek dla błędów HTTP 4xx/5xx

        # Logowanie sukcesu (można ograniczyć, by nie zaśmiecać logów Render)
        # print(f"[WEBHOOK_SUCCESS] Zapisano do Supabase: ID {data.get('id')}, Symbol {data.get('symbol') or data.get('ticker')}, Event {data.get('event') or data.get('type')}")
        return jsonify({"status": "success", "message": "Alert received and forwarded."}), 201 # 201 Created

    except requests.exceptions.HTTPError as http_err:
        error_details = http_err.response.text if http_err.response else "No response details"
        print(f"[WEBHOOK_ERROR] Błąd zapisu do Supabase (HTTP): {http_err.response.status_code} - {error_details}")
        return jsonify({"error": "Failed to store alert in database", "details": error_details}), 502 # Bad Gateway
    except requests.exceptions.RequestException as req_err:
        print(f"[WEBHOOK_ERROR] Błąd zapisu do Supabase (Sieciowy): {req_err}")
        return jsonify({"error": "Network error while contacting database"}), 504 # Gateway Timeout
    except Exception as e:
        print(f"[WEBHOOK_ERROR] Nieoczekiwany błąd: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    # Uruchomienie lokalne dla testów
    port = int(os.environ.get("PORT", 8000)) # Render może ustawić zmienną PORT
    app.run(host="0.0.0.0", port=port, debug=True) # debug=True tylko dla dewelopmentu