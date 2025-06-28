# Krok 1: Użyj tego samego lekkiego obrazu Pythona dla spójności
FROM python:3.11-slim

# Krok 2: Ustaw folder roboczy wewnątrz kontenera
WORKDIR /app

# Krok 3: Skopiuj plik z zależnościami i zainstaluj je
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Krok 4: Skopiuj cały kod aplikacji do kontenera
COPY . .

# Krok 5: Zdefiniuj komendę, która uruchomi APLIKACJĘ KOLEKTORA
# Ważne: Wskazujemy na nowy plik wejściowy 'collector_main.py' i jego aplikację 'app'
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 collector_main:app