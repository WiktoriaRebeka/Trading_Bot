# Lokalizacja: bot_service/Dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared_lib/ /app/shared_lib/
COPY bot_service/ /app/bot_service/

# --- TYMCZASOWA ZMIANA DO DEBUGOWANIA ---
# Zamiast Gunicorna, uruchamiamy skrypt bezpośrednio,
# aby złapać dokładny błąd startowy.
CMD [ "python", "-m", "bot_service.main" ]

