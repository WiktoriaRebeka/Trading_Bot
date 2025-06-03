# TRADING_BOT/app/main.py (TYMCZASOWY - TEST MINIMALNEGO FLASKA)
import os
from flask import Flask
import time # Dodajemy, żeby coś robić w pętli

print(f"[MAIN_MINIMAL_FLASK] START - Plik app/main.py, Wersja testowa 4.0")
print(f"[MAIN_MINIMAL_FLASK] GAE_ENV: {os.getenv('GAE_ENV')}")

flask_app = Flask(__name__)

@flask_app.route('/')
def very_simple_health_check():
    current_time = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    print(f"[MAIN_MINIMAL_FLASK] Żądanie na / o {current_time}")
    return f"Minimal Flask is running at {current_time}!", 200

# Na razie nie importujemy nic z Twojej logiki bota ani Firebase
# Nie uruchamiamy żadnych wątków

if __name__ == '__main__':
    # Ten blok nie jest używany przez Gunicorna w App Engine
    print("[MAIN_MINIMAL_FLASK] Uruchamianie serwera Flask lokalnie...")
    flask_app.run(host='0.0.0.0', port=8081)
else:
    # Ten blok może być wykonany, gdy Gunicorn importuje flask_app
    print(f"[MAIN_MINIMAL_FLASK] Moduł zaimportowany przez Gunicorna (prawdopodobnie). Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")