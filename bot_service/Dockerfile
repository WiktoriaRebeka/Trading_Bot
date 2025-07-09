# Lokalizacja: bot_service/Dockerfile

FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

# Kopiujemy i instalujemy zależności NAJPIERW, aby wykorzystać cache Dockera
COPY bot_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy bibliotekę współdzieloną
COPY shared_lib/ /app/shared_lib/

# --- TO JEST KLUCZOWA LINIA, KTÓREJ BRAKUJE ---
# Kopiujemy cały kod aplikacji bota do kontenera
COPY bot_service/ /app/bot_service/

# Polecenie uruchomieniowe
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 "bot_service.main:create_app()"