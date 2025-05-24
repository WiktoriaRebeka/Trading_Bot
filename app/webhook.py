# app/webhook.py
from flask import Flask, request, jsonify, Response
from datetime import datetime
import os
import json
import sqlite3

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "../alerts.db")
LOG_FILE = "alerts_log.jsonl"

# Tworzenie bazy danych i tabeli, jeśli nie istnieje
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    received_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()
conn.close()

@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "POST":
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400

        # Zapisz do bazy danych
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO alerts (payload) VALUES (?)", [json.dumps(data)])
        conn.commit()
        conn.close()

        # Opcjonalnie zapisz do log.jsonl
        data["received_at"] = datetime.now().isoformat()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

        return jsonify({"status": "✅ Alert zapisany"}), 200

    else:
        # GET: wyświetl ostatnie alerty
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT payload, received_at FROM alerts ORDER BY received_at DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()

        html = "<h2>📬 Ostatnie alerty (SQLite)</h2><pre style='background:#f5f5f5;padding:1em;'>"
        for payload, received_at in rows:
            try:
                obj = json.loads(payload)
                html += f"[{received_at}] {json.dumps(obj, ensure_ascii=False)}\n"
            except Exception:
                html += f"[{received_at}] {payload}\n"
        html += "</pre>"
        return Response(html, mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
