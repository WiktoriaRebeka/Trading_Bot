# Krok 1: Wybierz oficjalny, lekki obraz Pythona
FROM python:3.11-slim

# Krok 2: Ustaw folder roboczy wewnątrz kontenera
WORKDIR /app

# Krok 3: Skopiuj plik z zależnościami i zainstaluj je
# Robimy to jako osobny krok dla optymalizacji cache'u
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Krok 4: Skopiuj cały kod aplikacji do kontenera
COPY . .

# Krok 5: Zdefiniuj komendę, która uruchomi aplikację
# Używamy exec, aby gunicorn poprawnie odbierał sygnały systemowe
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app