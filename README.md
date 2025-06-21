# Automatyczny Bot Tradingowy v2 (Google Cloud Platform)

## 🧠 Projekt: Zautomatyzowany Bot Tradingowy oparty o Alerty z TradingView

Ten projekt to w pełni zautomatyzowany system tradingowy (24/7), który działa w całości w ekosystemie Google Cloud. Jego zadaniem jest realizowanie strategii tradingowej opartej na koncepcji Smart Money, w szczególności na blokach zleceń (Order Blocks).

---

## 🌐 Architektura Systemu

System składa się z dwóch głównych, niezależnych komponentów:

1.  **Webhook (Google Cloud Functions):**
    *   **Cel:** Błyskawiczne i niezawodne odbieranie sygnałów (alertów) wysyłanych z TradingView.
    *   **Technologia:** Bezserwerowa funkcja w Pythonie, która jest skalowalna i płaci się tylko za jej wywołanie.
    *   **Zadanie:** Odebrać alert w formacie JSON, dodać serwerowy znacznik czasu (`received_at`) i natychmiast zapisać go jako nowy dokument w bazie danych Cloud Firestore.

2.  **Główny Bot (Google App Engine):**
    *   **Cel:** Wykonywanie całej logiki analitycznej i tradingowej.
    *   **Technologia:** Aplikacja w Pythonie (z użyciem Flask) działająca na platformie App Engine.
    *   **Uruchamianie:** Bot jest aktywowany cyklicznie (np. co minutę) przez usługę **Cloud Scheduler**, co eliminuje potrzebę utrzymywania stałego, kosztownego połączenia.

---

### ⚙️ Przepływ Danych i Logika

1.  **TradingView** generuje alert (np. o nowym Order Blocku) i wysyła żądanie `POST` z danymi JSON na publiczny URL naszej funkcji w **Cloud Functions**.
2.  **Webhook** (`gcp-webhook/main.py`) natychmiast zapisuje surowy alert w kolekcji `alerts` w **Cloud Firestore**.
3.  **Cloud Scheduler** (zgodnie z harmonogramem, np. `*/1 * * * *`) wysyła żądanie `POST` na endpoint `/run-bot-cycle` naszej aplikacji w **App Engine**.
4.  **Główny Bot** (`app/main.py`) rozpoczyna swój cykl pracy:
    *   **Inicjalizacja:** Upewnia się, że ma połączenie z Firestore (`app/firebase_client.py`).
    *   **Pobieranie Danych:** Odczytuje z Firestore nowe, nieprzetworzone alerty, wykorzystując zapisany wcześniej znacznik czasu (`app/fetch_from_firestore.py`).
    *   **Zarządzanie Stanem:** Przetwarza nowe alerty i przechowuje aktywne setupy w pamięci RAM na czas jednego cyklu (`app/state_manager.py`).
    *   **Logika Tradingowa:**
        1.  Pobiera aktualne ceny rynkowe dla wszystkich monitorowanych symboli z publicznego API giełdy **Bybit** (`app/bot_logic.py`).
        2.  Dla każdego aktywnego setupu, porównuje aktualną cenę z ceną wejścia (`entry`) z alertu.
        3.  Podejmuje decyzję o "otwarciu" lub "zamknięciu" pozycji, logując te zdarzenia do dedykowanej kolekcji w Firestore (`app/positions_logger.py`).
5.  Po zakończeniu cyklu instancja App Engine może zostać wyłączona (dzięki `min_instances: 0`), aby zminimalizować koszty, i czeka na następne wywołanie od Cloud Scheduler.

---

### 🛠️ Narzędzia i Technologie

*   **Język:** Python 3.11 / Python 3.10
*   **Platforma Chmurowa:** Google Cloud Platform (GCP)
*   **Główne Usługi GCP:**
    *   **Google Cloud Functions:** Dla webhooka.
    *   **Google App Engine:** Dla głównego bota.
    *   **Cloud Firestore:** Baza danych NoSQL.
    *   **Cloud Scheduler:** Do cyklicznego uruchamiania bota.
    *   **IAM:** Do zarządzania uprawnieniami.
*   **Główne Biblioteki Python:**
    *   `google-cloud-firestore`: Do komunikacji z bazą danych.
    *   `Flask` & `gunicorn`: Do obsługi żądań HTTP w App Engine.
    *   `requests`: Do pobierania cen z API Bybit.

---

### 📁 Struktura Projektu
TRADING_BOT/
├── app/ # Kod Głównego Bota (dla App Engine)
│ ├── init.py
│ ├── main.py # Aplikacja Flask, endpoint /run-bot-cycle
│ ├── firebase_client.py
│ ├── bot_logic.py # Główna logika decyzyjna
│ ├── fetch_from_firestore.py
│ ├── state_manager.py
│ └── positions_logger.py
├── gcp-webhook/ # Kod Webhooka (dla Cloud Functions)
│ ├── main.py
│ └── requirements.txt
├── app.yaml # Konfiguracja App Engine
├── requirements.txt # Zależności dla Głównego Bota
└── .gcloudignore # Pliki ignorowane przy wdrożeniu do GCP



---

### 🚀 Jak Uruchomić i Wdrożyć

#### Wymagania Wstępne
1.  Zainstalowane [Google Cloud SDK](https://cloud.google.com/sdk/docs/install).
2.  Zalogowanie się i ustawienie projektu: `gcloud init` i `gcloud config set project trading-bot-463318`.

#### 1. Wdrożenie Webhooka (Cloud Function)
*   Wdrożenie wykonujemy tylko raz lub po zmianach w kodzie webhooka.
*   Przejdź do katalogu `gcp-webhook`:
    ```bash
    cd gcp-webhook
    ```
*   Uruchom komendę wdrożenia:
    ```bash
    gcloud functions deploy firestore_webhook_receiver --gen2 --runtime python311 --trigger-http --allow-unauthenticated --entry-point firestore_webhook_receiver --region=us-central1
    ```
*   Po wdrożeniu nadaj uprawnienia i skonfiguruj URL w TradingView.

#### 2. Wdrożenie Głównego Bota (App Engine)
*   Wdrożenie wykonujemy po każdej zmianie w logice bota (w folderze `app/`).
*   Przejdź do głównego katalogu projektu (`TRADING_BOT/`):
    ```bash
    cd .. 
    ```
*   Uruchom komendę wdrożenia (używając PowerShell):
    ```powershell
    gcloud app deploy app.yaml --version "v$(Get-Date -Format 'yyyyMMdd-HHmmss')" --no-cache
    ```

#### 3. Konfiguracja Cloud Scheduler
*   Skonfiguruj zadanie w panelu GCP, aby cyklicznie (`*/1 * * * *` dla każdej minuty) wysyłało żądanie `POST` na adres: `https://[ID-PROJEKTU].uc.r.appspot.com/run-bot-cycle`.
*   Upewnij się, że używasz uwierzytelniania `OIDC`.

---

### 📊 Status Projektu (Aktualny)

*   ✅ **Webhook:** Wdrożony i w pełni operacyjny.
*   ✅ **Główny Bot:** Wdrożony na App Engine.
*   ✅ **Cloud Scheduler:** Skonfigurowany do cyklicznego uruchamiania bota.
*   ⏳ **Aktualne Zadanie:** Debugowanie logiki otwierania i zamykania pozycji w `app/bot_logic.py` na podstawie logów z działającej aplikacji w App Engine.