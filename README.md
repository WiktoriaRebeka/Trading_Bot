Automatyczny Bot Tradingowy v2 (Google Cloud Platform)
🧠 Projekt: Zautomatyzowany Bot Tradingowy oparty o Alerty z TradingView
Ten projekt to w pełni zautomatyzowany system tradingowy (24/7), który działa w całości w ekosystemie Google Cloud. Jego zadaniem jest realizowanie strategii tradingowej opartej na koncepcji Smart Money, w szczególności na blokach zleceń (Order Blocks), zasilanej alertami z TradingView.
🌐 Architektura Systemu
System składa się z komponentów chmurowych zaprojektowanych z myślą o niezawodności, skalowalności i bezpieczeństwie.
Webhook (Google Cloud Functions):
Cel: Błyskawiczne i niezawodne odbieranie sygnałów (alertów) wysyłanych z TradingView.
Technologia: Bezserwerowa funkcja w Pythonie.
Zadanie: Odebrać alert JSON, dodać serwerowy znacznik czasu (received_at) i natychmiast zapisać go w bazie Cloud Firestore.
Główny Bot (Google App Engine):
Cel: Wykonywanie całej logiki analitycznej i tradingowej.
Technologia: Aplikacja w Pythonie (Flask) na platformie App Engine, skonfigurowana do minimalizacji kosztów (min_instances: 0).
Uruchamianie: Aktywowany cyklicznie (co minutę) przez usługę Cloud Scheduler.
Infrastruktura Sieciowa (VPC & NAT):
Problem: Giełdy takie jak Bybit często blokują żądania przychodzące bezpośrednio z adresów IP centrów danych Google.
Rozwiązanie: Skonfigurowano zaawansowaną architekturę sieciową, aby ominąć te ograniczenia.
VPC Connector: Tworzy "prywatny most" łączący aplikację App Engine z naszą siecią VPC.
Cloud Router & Cloud NAT: Zapewniają aplikacji kontrolowane, "cywilne" wyjście do internetu przez publiczny adres IP, który nie jest blokowany przez API giełdy.
Automatyczne Wdrożenie (GitHub Actions):
Cel: Pełna automatyzacja procesu wdrażania (CI/CD).
Mechanizm: Każde wypchnięcie (git push) zmian do gałęzi div w repozytorium GitHub automatycznie uruchamia proces, który buduje i wdraża nową wersję aplikacji na App Engine. Eliminuje to problemy z lokalnym środowiskiem i gwarantuje spójność.
⚙️ Przepływ Danych i Logika
TradingView generuje alert i wysyła żądanie POST na publiczny URL naszej funkcji w Cloud Functions.
Webhook natychmiast zapisuje alert w kolekcji alerts w Cloud Firestore.
Cloud Scheduler (zgodnie z harmonogramem */1 * * * *) wysyła żądanie POST na endpoint /run-bot-cycle naszej aplikacji w App Engine.
Główny Bot rozpoczyna swój cykl pracy:
Inicjalizacja: Łączy się z Firestore.
Pobieranie Danych: Odczytuje z Firestore nowe, nieprzetworzone alerty.
Zarządzanie Stanem: Przetwarza alerty i zarządza aktywnymi setupami w pamięci RAM.
Logika Tradingowa:
Pobiera aktualne ceny rynkowe z API Bybit. Żądanie wychodzi z App Engine, przechodzi przez VPC Connector do Cloud NAT, a następnie trafia do Bybit z publicznego, niezablokowanego adresu IP.
Porównuje aktualną cenę z danymi z alertu.
Podejmuje decyzję o "otwarciu" lub "zamknięciu" pozycji, logując zdarzenia do dedykowanej kolekcji w Firestore.
Po zakończeniu cyklu instancja App Engine może zostać wyłączona, aby zminimalizować koszty.
🛠️ Narzędzia i Technologie
Język: Python 3.11
Platforma Chmurowa: Google Cloud Platform (GCP)
Główne Usługi GCP:
Google Cloud Functions: Dla webhooka.
Google App Engine: Dla głównego bota.
Cloud Firestore: Baza danych NoSQL.
Cloud Scheduler: Do cyklicznego uruchamiania bota.
VPC, VPC Connector, Cloud Router, Cloud NAT: Dla zaawansowanej obsługi sieci.
IAM: Do zarządzania uprawnieniami.
Automatyzacja: GitHub Actions (dla CI/CD).
Główne Biblioteki Python:
google-cloud-firestore, Flask, gunicorn, requests.
📁 Struktura Projektu (Aktualna)
Struktura została uproszczona, aby zapewnić niezawodność wdrożeń.

TRADING_BOT/
├── .github/
│   └── workflows/
│       └── deploy.yml      # Definicja automatycznego wdrożenia
├── app/
│   ├── __init__.py         # Kluczowy plik oznaczający pakiet Python
│   ├── main.py             # Główna aplikacja Flask
│   ├── bot_logic.py
│   ├── firebase_client.py
│   ├── requirements.txt    # Zależności dla bota
│   └── ... (pozostałe pliki .py)
├── gcp-webhook/
│   └── ... (kod webhooka bez zmian)
│
├── app.yaml                # Konfiguracja App Engine (z vpc_access_connector)
├── .gcloudignore
└── .gitignore


🚀 Proces Wdrożenia (Zautomatyzowany)
Wdrożenie lokalne za pomocą gcloud app deploy nie jest już zalecane z powodu problemów ze środowiskiem.
Główna metoda wdrażania:
Wprowadź zmiany w kodzie na swoim lokalnym komputerze.
Zapisz zmiany w systemie Git:
git add .
git commit -m "Opis wprowadzonych zmian"

Wypchnij zmiany do zdalnego repozytorium na GitHub:
git push

Gotowe! GitHub Actions automatycznie wykryje zmiany, uruchomi proces budowy i wdroży nową wersję aplikacji na App Engine. Postęp można śledzić w zakładce "Actions" w repozytorium GitHub.
📊 Status Projektu (Aktualny)
✅ Infrastruktura: Wdrożona i w pełni operacyjna, włącznie z zaawansowaną konfiguracją sieciową (VPC, NAT).
✅ Automatyzacja: Działający pipeline CI/CD z GitHub Actions.
✅ Bot: Pomyślnie uruchamia się, łączy z Firestore i pobiera dane.
✅ Problem z Połączeniem: Błąd 403 Forbidden z API Bybit został rozwiązany przez implementację Cloud NAT.
🎯 Aktualne Zadanie: Pełna weryfikacja logiki tradingowej (bot_logic.py) i monitorowanie działania na żywo. Projekt jest teraz w fazie testów funkcjonalnych.