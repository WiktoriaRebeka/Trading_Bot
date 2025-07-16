import os
import sys
import logging
from flask import Flask, jsonify
from google.cloud import firestore

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

@app.route('/')
def test_firestore_connection():
    try:
        logging.info("Attempting to initialize Firestore client...")
        db = firestore.Client(
            project="trading-bot-463318",
            database="trading-bot-data"
        )
        logging.info("Client initialized. Attempting to read a document...")
        
        # Próba odczytu znanego dokumentu
        doc_ref = db.collection("bot_config").document("symbols_config")
        doc = doc_ref.get()
        
        if doc.exists:
            logging.info(f"SUCCESS! Read document: {doc.to_dict()}")
            return jsonify({"status": "success", "data": doc.to_dict()}), 200
        else:
            logging.error("FAILURE! Document 'symbols_config' does not exist.")
            return jsonify({"status": "error", "message": "Document not found"}), 500

    except Exception as e:
        logging.critical(f"FATAL ERROR: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)