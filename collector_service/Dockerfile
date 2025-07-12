# Lokalizacja: /collector_service/Dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

# Kopiujemy i instalujemy zależności NAJPIERW, aby wykorzystać cache Dockera
COPY collector_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy kod aplikacji
COPY shared_lib/ /app/shared_lib/
COPY collector_service/ /app/collector_service/

# Polecenie uruchomieniowe dla Gunicorna
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 "collector_service.collector_main:create_app()"