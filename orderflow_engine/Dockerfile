# orderflow_engine/Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED 1
WORKDIR /app
# PYTHONPATH mówi Pythonowi, że ma szukać modułów w folderze /app
ENV PYTHONPATH=/app

# Instalacja zależności
COPY orderflow_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiowanie kodu (shared_lib musi być dostępne dla engine)
COPY shared_lib/ ./shared_lib/
COPY orderflow_engine/ ./orderflow_engine/

# Uruchomienie serwera (PORT z env Cloud Run, domyślnie 8080)
CMD ["sh", "-c", "uvicorn orderflow_engine.main:app --host 0.0.0.0 --port ${PORT:-8080}"]