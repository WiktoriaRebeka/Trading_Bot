# app/webhook.py

from flask import Flask, request, jsonify, Response
from datetime import datetime
import os, json

app = Flask(__name__)
LOG_FILE = "alerts_log.jsonl"
HTML_LOG = "alerts_log.html"
TIMEZONE = "Europe/Warsaw"

os.environ["TZ"] = TIMEZONE

def log_json(data: dict):
    data["received_at"] = datetime.now().isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "POST":
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400

        log_json(data)
        return jsonify({"status": "✅ JSON alert zapisany"}), 200

    else:
        if not os.path.exists(LOG_FILE):
            return "<h2>📬 Brak alertów jeszcze.</h2>"

        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        html = "<h2>📬 Ostatnie alerty (JSON)</h2><pre style='background:#f5f5f5;padding:1em;border-radius:6px;font-size:0.9em;'>"
        for line in lines[-50:]:
            html += line
        html += "</pre>"

        return Response(html, mimetype='text/html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
