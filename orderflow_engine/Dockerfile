FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy cały folder orderflow_engine jako pakiet
COPY . /app/orderflow_engine/

WORKDIR /app/orderflow_engine/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]