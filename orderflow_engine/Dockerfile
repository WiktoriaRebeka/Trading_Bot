# Lokalizacja: orderflow_engine/Dockerfile

FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

COPY orderflow_engine/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared_lib/ /app/shared_lib/
COPY orderflow_engine/ /app/orderflow_engine/

CMD ["uvicorn", "orderflow_engine.main:app", "--host", "0.0.0.0", "--port", "8080"]