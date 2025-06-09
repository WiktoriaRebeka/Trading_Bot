# hello_main.py
from flask import Flask
import logging # Dodajemy logowanie dla Gunicorna
import sys     # Dodajemy logowanie dla Gunicorna

# Podstawowe logowanie, aby zobaczyć, czy Gunicorn w ogóle ładuje ten plik
# To pojawi się w logach App Engine, jeśli Gunicorn to uruchomi
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
_logger = logging.getLogger(__name__)
_logger.info("--- HELLO_MAIN.PY LOADED ---")


app = Flask(__name__)
_logger.info("--- FLASK APP 'app' CREATED IN HELLO_MAIN.PY ---")


@app.route('/')
def hello():
    _logger.info("--- Handling request for / in hello_main.py ---")
    return 'Hello World from App Engine (Test B)!'

# Ten blok nie jest potrzebny na App Engine z Gunicornem, ale nie zaszkodzi
if __name__ == '__main__':
    _logger.info("--- Running hello_main.py locally (should not happen on App Engine) ---")
    app.run(host='127.0.0.1', port=8080, debug=True)