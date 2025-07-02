# Lokalizacja: collector_service/Dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1

WORKDIR /app
ENV PYTHONPATH /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared_lib/ /app/shared_lib/
COPY collector_service/ /app/collector_service/

# Wskazujemy na funkcję 'create_app' w module 'collector_main'.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 "collector_service.collector_main:create_app()"