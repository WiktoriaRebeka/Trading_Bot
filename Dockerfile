# Krok 1: Użyj oficjalnego, lekkiego obrazu Pythona
FROM python:3.11-slim

# Krok 2: Ustaw zmienne środowiskowe, aby uniknąć buforowania
ENV PYTHONUNBUFFERED 1

# Krok 3: Ustaw folder roboczy
WORKDIR /app

# Krok 4: Skopiuj plik z zależnościami i zainstaluj je
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Krok 5: Skopiuj cały kod aplikacji.
# To jest prostsze i bardziej niezawodne niż kopiowanie pojedynczych plików.
# Jeśli obraz będzie za duży, można dodać plik .dockerignore.
COPY . .

# Krok 6: Zdefiniuj komendę, która uruchomi APLIKACJĘ KOLEKTORA używając fabryki.
# ### POPRAWKA: Zmieniono collector_main:app na "collector_main:create_app()" ###
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 "collector_main:create_app()"