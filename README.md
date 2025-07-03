# Automatyczny Bot Tradingowy v8.0 (Google Cloud Platform)

## 1. Opis Projektu i Cel Główny

Automated Trading Bot v8.0 to w pełni zautomatyzowany, bezserwerowy system tradingowy działający 24/7 na platformie Google Cloud (GCP). Jego głównym celem jest autonomiczne wykonywanie i zaawansowana analiza strategii tradingowej opartej na koncepcji "Smart Money", w szczególności na formacjach typu Order Block (OB).

System został zaprojektowany z myślą o maksymalnej niezawodności, bezpieczeństwie i wydajności poprzez **rozdzielenie procesu zbierania danych od rdzenia logiki tradingowej**. Dąży do minimalizacji kosztów operacyjnych i zapewnienia precyzji analitycznej dzięki solidnej, wielokomponentowej architekturze bezserwerowej, gdzie cała logika jest wdrożona jako skonteneryzowane mikroserwisy w usłudze Cloud Run.

## 2. Cykl Operacyjny i Rdzeń Logiki Biznesowej

System działa w oparciu o precyzyjnie zdefiniowany, wieloetapowy przepływ danych i decyzji:

**Etap 1: Generowanie Sygnału (TradingView)**
*   **Źródło:** Niestandardowy wskaźnik w języku Pine Script na platformie TradingView.
*   **Logika wskaźnika:** Wskaźnik analizuje wykres cenowy w czasie rzeczywistym w poszukiwaniu formacji New OB (Nowy Blok Zleceń).
*   **Akcja:** Po zidentyfikowaniu nowego, ważnego OB, wskaźnik wyzwala alert za pomocą webhooka.

**Etap 2: Przyjmowanie i Kolejkowanie Alertów (GCP)**
*   **Usługa:** Lekka funkcja Cloud Function (`gcp-webhook`) działająca jako endpoint dla webhooka.
*   **Przepływ:**
    1.  Webhook z TradingView wysyła żądanie POST z danymi alertu (JSON) na publiczny URL funkcji.
    2.  Funkcja natychmiast zapisuje surowy alert jako nowy dokument w kolekcji `alerts` w bazie Cloud Firestore, która służy jako trwała kolejka wejściowa.

**Etap 3: Wysokowydajne Zbieranie Danych (Niezależny Mikroserwis na Cloud Run)**
Aby zapewnić niezawodność i zniwelować opóźnienia API, dedykowany serwis zbierający dane działa niezależnie i asynchronicznie.
*   **Usługa:** Skonteneryzowana aplikacja w Pythonie (`data-collector-service`) w Cloud Run, wyzwalana co minutę przez Cloud Scheduler.
*   **Przepływ:**
    1.  Serwis odczytuje dynamiczną listę symboli do monitorowania z dokumentu konfiguracyjnego w Firestore (`bot_config/symbols_config`). Pozwala to na aktualizacje na żywo bez potrzeby wdrażania nowego kodu.
    2.  **Równolegle i asynchronicznie** odpytuje API Bybit o najnowsze 1-minutowe świece (kline) dla wszystkich symboli, drastycznie skracając czas zbierania danych.
    3.  Zapisuje ceny high, low i close dla każdego symbolu do dedykowanej kolekcji w Firestore: `latest_klines`. Ta kolekcja działa jak szybki i niezawodny wewnętrzny cache danych.

**Etap 4: Cykliczne Przetwarzanie i Egzekucja (Główny Bot na Cloud Run)**
*   **Orkiestracja:** Oddzielne zadanie Cloud Scheduler wywołuje główny serwis bota (`trading-bot-service`) co minutę.
*   **Przepływ Głównego Bota:**
    *   **4.1. Zarządzanie Setupami:** Bot odczytuje nowe alerty z kolejki `alerts` i waliduje je przy użyciu modeli Pydantic. Każdy nowy alert dla danego symbolu nadpisuje poprzedni "aktywny setup" w kolekcji `active_setups`, unieważniając Stary OB (Old OB).
    *   **4.2. Otwieranie Nowych Pozycji:**
        *   **Źródło danych:** Bot odczytuje najnowsze dane cenowe dla wszystkich monitorowanych symboli **bezpośrednio z cache'u `latest_klines` w Firestore**. Nie odpytuje już API Bybit, co czyni proces szybszym i bardziej odpornym na błędy.
        *   **Logika decyzyjna:** Iteruje po wszystkich aktywnych setupach i na podstawie zbuforowanych cen high i low ostatniej świecy, decyduje, czy otworzyć nową pozycję.
        *   **Zarządzanie stanem:** Poprawnie identyfikuje Świeży OB (Fresh OB - pierwsze wejście) vs. Użyty OB (Used OB - kolejne wejścia) i obsługuje logikę resetu ceny po stracie (LOSE). Po wejściu tworzy odizolowany dokument w kolekcji `open_trades`, "zamrażając" wszystkie parametry transakcji (SL, TP itp.).
    *   **4.3. Monitorowanie Otwartych Pozycji:** Bot iteruje po wszystkich dokumentach w kolekcji `open_trades`. Używa świeżych danych z cache'u Firestore, aby sprawdzić, czy "zamrożone" poziomy SL lub TP którejkolwiek z pozycji zostały naruszone.
    *   **4.4. Finalizacja i Zoptymalizowana Analiza Post-Mortem:**
        *   **Początkowy zapis do BigQuery:** Gdy pozycja jest zamykana (WIN/LOSE), bot pobiera historyczne dane kline dla czasu trwania transakcji, oblicza maksymalne osiągnięte R:R i zapisuje kompletny, początkowy rekord do BigQuery.
        *   **Tworzenie "Ducha":** "Duch" zamkniętej transakcji jest tworzony w kolekcji `analyzed_trades` w Firestore, przechowując jej początkowy stan.
        *   **Wydajne Śledzenie Pasywne:** W kolejnych cyklach, bot optymalnie śledzi tego "ducha". Zamiast pobierać całą historię transakcji, pobiera tylko nowe świece od ostatniego sprawdzenia. Jeśli cena osiągnie nowy, wyższy poziom TP, wysyła bezpieczne, sparametryzowane zapytanie `UPDATE` do BigQuery, aby wzbogacić istniejący rekord. Proces ten trwa do momentu osiągnięcia `tp_5_0` lub pierwotnego `sl`, po czym "duch" jest usuwany.

## 3. Architektura Techniczna

System wykorzystuje rozdzieloną, bezpieczną i wysokowydajną architekturę mikroserwisów na platformie GCP.

*   **Przyjmowanie Danych:** TradingView (Webhook) -> Cloud Function -> Cloud Firestore (kolekcja `alerts`).
*   **Zbieranie Danych (Cache):** Cloud Scheduler -> Cloud Run (`data-collector-service`) -> Bybit API -> Cloud Firestore (kolekcja `latest_klines`).
*   **Orkiestracja:** Cloud Scheduler (1-minutowe wyzwalacze cron dla obu serwisów).
*   **Rdzeń Aplikacji:** Cloud Run (kontenery Docker dla `trading-bot-service` i `data-collector-service`), zbudowane z użyciem wzorca Application Factory dla solidności.
*   **Bezpieczeństwo:**
    *   **GCP Secret Manager:** Wszystkie wrażliwe dane (jak klucze API) są przechowywane bezpiecznie i dostępne poprzez role IAM, a nie w kodzie czy plikach `.env` w środowisku produkcyjnym.
    *   **Zapytania sparametryzowane:** Wszystkie operacje `UPDATE` na BigQuery są sparametryzowane, aby zapobiec podatnościom SQL Injection.
*   **Zarządzanie Stanem (Firestore):**
    *   `bot_config`: Przechowuje dynamiczną konfigurację aplikacji, taką jak lista symboli do obserwacji.
    *   `alerts`: Trwała kolejka dla przychodzących sygnałów.
    *   `latest_klines`: Cache cenowy w czasie rzeczywistym, aktualizowany przez kolektor.
    *   `active_setups`, `open_trades`, `analyzed_trades`: Kolekcje zarządzające stanem logiki tradingowej.
*   **Analityka i Logowanie:**
    *   **Cloud Logging:** Centralny hub do monitorowania w czasie rzeczywistym, ze strukturalnymi nazwami logów dla łatwego filtrowania.
    *   **BigQuery:** Analityczna hurtownia danych dla wszystkich zamkniętych transakcji.
*   **Infrastruktura Sieciowa:**
    *   **Serverless VPC Access Connector i Cloud NAT:** Zapewniają stały adres IP dla całego ruchu wychodzącego.
*   **Automatyzacja Wdrożeń (CI/CD):**
    *   **GitHub Actions i Cloud Build:** Workflowy w GitHub Actions wyzwalają budowanie w Google Cloud Build. Każdy serwis ma dedykowany plik konfiguracyjny `cloudbuild-*.yaml`, aby zapewnić poprawne i odizolowane budowanie, używając odpowiedniego pliku `Dockerfile`.

## 4. Kluczowe Zasady Projektowe

*   **Rozdzielenie (Decoupling):** Zbieranie danych jest w pełni oddzielone od logiki tradingowej. Główny bot jest odporny na awarie API, ponieważ opiera się na wewnętrznym cache'u.
*   **Bezpieczeństwo Przede Wszystkim:** Sekrety są zarządzane przez GCP Secret Manager, a zapytania do bazy danych są zabezpieczone przed atakami injection.
*   **Wysoka Wydajność:** Asynchroniczne, równoległe pobieranie danych w kolektorze minimalizuje opóźnienia.
*   **Solidność i Odporność na Błędy:** Aplikacja jest napisana defensywnie, używając modeli Pydantic do walidacji danych i wzorca Application Factory, aby zapobiegać problemom z zarządzaniem procesami przez Gunicorn.
*   **Elastyczność:** Lista handlowanych symboli jest zarządzana dynamicznie w Firestore bez konieczności zmian w kodzie.

## 5. Struktura Projektu

Projekt jest zorganizowany w dedykowane, odizolowane katalogi dla każdego mikroserwisu, bibliotekę współdzieloną oraz funkcję webhooka.
.
├── .github/
│ └── workflows/
│ ├── deploy-collector.yml # Workflow dla kolektora danych
│ └── deploy.yml # Workflow dla głównego bota
├── bot_service/ # Kod głównego bota tradingowego
│ ├── init.py
│ ├── Dockerfile
│ ├── main.py
│ ├── bot_logic.py
│ └── ...
├── collector_service/ # Kod mikroserwisu zbierającego dane
│ ├── init.py
│ ├── Dockerfile
│ ├── collector_main.py
│ └── data_collector.py
├── shared_lib/ # Współdzielony kod (modele, klienci, stałe)
│ ├── init.py
│ ├── models.py
│ ├── firebase_client.py
│ └── ...
├── gcp-webhook/ # Kod izolowanej funkcji Cloud Function
│ ├── main.py
│ └── requirements.txt
├── .env.example # Przykładowy plik zmiennych środowiskowych
├── .gitignore
├── cloudbuild-bot.yaml # Konfiguracja Cloud Build dla bota
├── cloudbuild-collector.yaml # Konfiguracja Cloud Build dla kolektora
├── README.md
└── requirements.txt # Centralny plik zależności dla obu serwisów


### Kluczowe Opisy Plików:

*   **`bot_service/main.py` i `collector_service/collector_main.py`**: Punkty wejścia (Flask) dla obu serwisów, używające wzorca Application Factory.
*   **`bot_service/bot_logic.py`**: "Mózg" systemu, zawierający rdzeń logiki biznesowej.
*   **`collector_service/data_collector.py`**: Rdzeń logiki dla kolektora danych, wykonujący asynchroniczne pobieranie danych.
*   **`shared_lib/`**: Katalog zawierający moduły współdzielone przez oba mikroserwisy:
    *   **`config_loader.py`**: Moduł do bezpiecznego ładowania konfiguracji z GCP Secret Manager lub lokalnego pliku `.env`.
    *   **`models.py`**: Definiuje modele danych Pydantic, zapewniając integralność danych i poprawiając czytelność kodu.
    *   **`firebase_client.py`, `bigquery_logger.py`**: Moduły abstrahujące komunikację z usługami GCP.
    *   **`constants.py`**: Centralne miejsce na stałe i parametry konfiguracyjne.
*   **`gcp-webhook/main.py`**: Izolowany kod dla funkcji Cloud Function przyjmującej alerty.
*   **`.../Dockerfile`**: Definicje do budowania obrazów kontenerów dla każdego serwisu.
*   **`cloudbuild-*.yaml`**: Dedykowane pliki konfiguracyjne dla Google Cloud Build, zapewniające poprawne budowanie każdego serwisu.
*   **`.github/workflows/*.yml`**: Definicje potoku CI/CD, które wyzwalają odpowiednie konfiguracje Cloud Build na podstawie zmienionych plików.