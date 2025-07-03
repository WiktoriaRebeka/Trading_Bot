# Lokalizacja: bot_service/Dockerfile

FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared_lib/ /app/shared_lib/
COPY bot_service/ /app/bot_service/

# --- PRZYWRÓCENIE POLECENIA PRODUKCYJNEGO ---
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 "bot_service.main:create_app()"