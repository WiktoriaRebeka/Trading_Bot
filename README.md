# Trading BOT using Liquidity Heatmap and Market Structure

🧠 Projekt: BOT Tradingowy oparty o alerty z TradingView

🔍 Cel:
Stworzenie BOTa działającego 24/7, który:
- odbiera alerty z TradingView (Market Structure & Liquidity Heatmap),
- analizuje dane, porównuje poziomy cen ze wskaźników,
- automatycznie podejmuje decyzje tradingowe przez API Bybit (wejście i wyjście z pozycji).

🌐 Architektura:
- Backend: Python (Flask + pybit + dotenv + requests + sqlite3)
- Frontend: HTML / TailwindCSS / jsPDF
- Webhook: `https://ekoenergiadomowa.com/webhook_sqlite.php`
- API giełdy: Bybit REST API (via pybit)
- TradingView: alerty webhook JSON (Market Structure & Heatmap)
- Baza danych: SQLite (`alerts.db`)

📥 Wejścia:
1. **Alerty TradingView** w formacie JSON, z dwóch wskaźników:
   - Market Structure (`OrderBlock`, `entry`, `stoploss`, `direction`)
   - Liquidity Heatmap (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
2. Alerty zawierają dane:
   - symbol (np. BTCUSDT),
   - timestamp (w strefie Europe/Warsaw),
   - poziomy cen (`entry`, `sl`, `TP`, `levelHigh`, `levelLow`)

⚙️ Logika BOTa:
1. TradingView wysyła alert JSON do webhooka PHP.
2. Webhook `webhook_sqlite.php` zapisuje alert do bazy `alerts.db`.
3. Skrypt `fetch_from_sqlite.py` co 60 sek. pobiera nowe alerty jako JSON z endpointu PHP.
4. Każdy alert przetwarzany przez `process_alert()` i zapisany w RAM (`state_manager.py`).
5. BOT porównuje alerty:
   - Jeśli `entry` z OrderBlock znajduje się w pobliżu topGreen / bottomRed:
     - LONG: TOP_GREEN w zakresie OrderBlocku
     - SHORT: BOTTOM_RED w zakresie OrderBlocku
6. BOT ustawia pozycję Limit + Stop Loss + TP + lewar (obliczany ze SL).
7. Monitoruje cenę aktywnej pozycji.
8. Pozycje nieaktywne usuwa, gdy warunki nie są już spełnione.
9. Wszystko jest logowane.

🧾 Dodatkowo:
- Bot działa 24/7 w Google Cloud Run lub innej chmurze

📦 Biblioteki:
Python: `flask`, `requests`, `python-dotenv`, `pybit`, `reportlab`, `sqlite3`  
Node: `axios`, `tailwindcss`, `jspdf`  
GitHub: `https://github.com/WiktoriaRebeka/Trading_Bot`

🛠 Status:
✅ Projekt rozpoczęty  
✅ Repo aktywne  
✅ Baza działa w SQLite  
🧠 Integracja i analiza alertów w toku

---

### 🧩 webhook_sqlite.php
- Obsługuje `POST` (TradingView) i zapisuje alert do `alerts.db`
- Obsługuje `GET` (HTML i JSON)
- Zamienia `timestamp` na `Europe/Warsaw`

### 🔁 state_manager.py
- Bufory RAM:
  - Heatmapa: 5 alertów (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
  - MarketStructure: 2 `OrderBlock`
- Funkcje:
  - `update_alert(alert)` – buforowanie
  - `print_debug()` – diagnostyka

### 🔄 fetch_from_sqlite.py
- Odczyt JSON z `webhook_sqlite.php?format=json`
- Wykrywa nowe alerty po `id`
- Zapisuje do `alerts_sqlite.jsonl`
- Przekazuje alerty do `process_alert()`

