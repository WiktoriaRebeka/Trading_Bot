# TRADING_BOT/main.py
import logging
import sys
from flask import Flask

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("[MINIMAL_FLASK_NO_GRPC] START Flask app (python311, no grpc).")
app = Flask(__name__)

@app.route('/')
def hello():
    logger.info("[MINIMAL_FLASK_NO_GRPC] Żądanie na /")
    return "Hello from Minimal Flask (python311, no grpc)!"

logger.info("[MINIMAL_FLASK_NO_GRPC] Flask app initialized.")