# Lokalizacja: bot_service/Dockerfile

# Używamy oficjalnego, lekkiego obrazu Python 3.11
FROM python:3.11-slim

# Ustawiamy zmienne środowiskowe dla optymalnego działania w kontenerze
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH /app

# Krok 1: Kopiujemy i instalujemy zależności NAJPIERW.
# To wykorzystuje cache Dockera i znacznie przyspiesza kolejne buildy,
# jeśli zależności się nie zmieniły.
COPY bot_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Krok 2: Kopiujemy kod aplikacji.
# Najpierw bibliotekę współdzieloną, potem kod specyficzny dla serwisu.
COPY shared_lib/ /app/shared_lib/
COPY bot_service/ /app/bot_service/

# Krok 3: Definiujemy polecenie startowe dla kontenera.
# Używamy Gunicorna, który jest standardem produkcyjnym dla aplikacji Flask.
# Nasłuchujemy na porcie podanym przez Cloud Run w zmiennej $PORT.
# 'exec' zapewnia prawidłowe zarządzanie procesem.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 "bot_service.main:create_app()"