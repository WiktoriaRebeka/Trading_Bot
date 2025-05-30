from flask import Flask, request, jsonify
from datetime import datetime
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = "https://cceowvpxutqxnaypnopr.supabase.co"
SUPABASE_API_KEY = os.getenv("SUPABASE_ANON_KEY")  # dodaj to do .env
TABLE_NAME = "alerts"

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    # Dodaj znacznik czasu
    data["received_at"] = datetime.utcnow().isoformat()

    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}",
        headers=headers,
        data=json.dumps(data)
    )

    if response.status_code in [200, 201]:
        print(f"[✅] Zapisano do Supabase: {data.get('symbol')} | {data.get('event')}")
        return jsonify({"status": "success"}), 200
    else:
        print(f"[❌] Błąd zapisu: {response.status_code} | {response.text}")
        return jsonify({"error": "Supabase error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
