from flask import Flask, request, jsonify, Response
from datetime import datetime
import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

def get_conn():
    return psycopg2.connect(
        host=os.getenv("SUPABASE_HOST"),
        port=os.getenv("SUPABASE_PORT"),
        dbname=os.getenv("SUPABASE_DB"),
        user=os.getenv("SUPABASE_USER"),
        password=os.getenv("SUPABASE_PASSWORD")
    )

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "POST":
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400

        save_to_supabase(data)
        return jsonify({"status": "✅ Alert zapisany do Supabase"}), 200

    # GET
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, symbol, event, value, direction, entry, stoploss, target, timestamp, received_at FROM alerts ORDER BY received_at DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()

    output = []
    for row in rows:
        output.append({
            "id": row[0],
            "symbol": row[1],
            "event": row[2],
            "value": row[3],
            "direction": row[4],
            "entry": row[5],
            "stoploss": row[6],
            "target": row[7],
            "timestamp": row[8].isoformat() if row[8] else None,
            "received_at": row[9].isoformat() if row[9] else None,
        })

    return jsonify(output)

def save_to_supabase(data):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO alerts (symbol, event, value, direction, entry, stoploss, target, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data.get("symbol") or data.get("ticker"),
            data.get("event") or data.get("type"),
            float(data.get("value", 0)) if data.get("value") else None,
            data.get("direction"),
            float(data.get("entry", 0)) if data.get("entry") else None,
            float(data.get("stoploss", 0)) if data.get("stoploss") else None,
            float(data.get("TP", 0)) if data.get("TP") else None,
            data.get("timestamp")
        ))
        conn.commit()
        conn.close()
        print(f"[✅] Zapisano alert: {data.get('symbol')} | {data.get('event')}")
    except Exception as e:
        print(f"[❌] Błąd zapisu do Supabase: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
