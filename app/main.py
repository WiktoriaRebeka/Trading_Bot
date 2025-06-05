from flask import Flask
import logging
import sys
import os
import time

# Skonfiguruj logowanie, aby wysyłać logi do stdout, co App Engine powinien przechwycić
# Jest to ważne, bo Gunicorn i App Engine przechwytują stdout/stderr.
logging.basicConfig(stream=sys.stdout, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - [FLASK_MINIMAL_TEST] - %(message)s')

# Możesz też uzyskać loggera specyficznie dla tego modułu
logger = logging.getLogger(__name__)

logger.info("--- SCRIPT main.py LOADED ---")
logger.info(f"Python version: {sys.version}")
logger.info(f"Platform: {sys.platform}")
logger.info(f"GAE_ENV: {os.getenv('GAE_ENV')}")
logger.info(f"GAE_RUNTIME: {os.getenv('GAE_RUNTIME')}")
logger.info(f"GAE_SERVICE: {os.getenv('GAE_SERVICE')}")
logger.info(f"GAE_VERSION: {os.getenv('GAE_VERSION')}")
logger.info(f"PORT (env): {os.getenv('PORT')}") # Gunicorn użyje tego portu
logger.info(f"CWD: {os.getcwd()}")
logger.info(f"sys.path: {sys.path}")
logger.info(f"Czas UTC startu modułu: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

# Utwórz instancję aplikacji Flask
# Nazwa 'app' jest tym, czego szuka Gunicorn w 'main:app'
app = Flask(__name__)
logger.info("Flask app instance created.")

@app.route('/')
def hello():
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    logger.info(f"Handling request for '/' at {timestamp}")
    return f"Hello from Minimal Flask App at {timestamp}!", 200

@app.route('/_ah/warmup')
def warmup():
    # Warmup requesty są wysyłane przez App Engine do instancji przed skierowaniem ruchu.
    # Dobrze jest je obsłużyć, aby uniknąć błędów w logach.
    logger.info("Handling warmup request /_ah/warmup")
    # Tutaj można umieścić logikę inicjalizacyjną, jeśli jest potrzebna.
    return '', 200

# Ten blok nie jest wykonywany, gdy Gunicorn uruchamia aplikację,
# ale może być przydatny do testów lokalnych.
if __name__ == '__main__':
    logger.info("Running Flask app locally (this shouldn't happen in App Engine with Gunicorn)...")
    # PORT jest ustawiany przez App Engine; lokalnie można użyć innego
    local_port = int(os.environ.get("PORT", 8080)) 
    app.run(host='0.0.0.0', port=local_port, debug=True)
else:
    # Ten blok jest bardziej prawdopodobny do wykonania, gdy Gunicorn importuje 'app'
    logger.info(f"Script {__name__} imported, likely by Gunicorn.")

logger.info("--- END OF SCRIPT main.py DEFINITIONS ---")