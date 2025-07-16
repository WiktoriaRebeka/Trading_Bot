FROM python:3.11-slim
ENV PYTHONUNBUFFERED 1
WORKDIR /app
COPY test_firestore/requirements.txt .
RUN pip install -r requirements.txt
COPY test_firestore/main.py .
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]