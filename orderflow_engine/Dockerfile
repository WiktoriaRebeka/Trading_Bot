FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Ustawiamy katalog roboczy na folder orderflow_engine
WORKDIR /orderflow_engine

# Kopiujemy requirements
COPY orderflow_engine/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy cały folder orderflow_engine
COPY orderflow_engine/ .

# Uruchamiamy FastAPI
CMD ["uvicorn", "orderflow_engine.main:app", "--host", "0.0.0.0", "--port", "8080"]