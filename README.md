# Trading BOT using Liquidity Heatmap and Market Structure

🧠 Projekt: BOT Tradingowy oparty o alerty z TradingView

🔍 Cel:
Stworzenie BOTa działającego 24/7, który:
- odbiera alerty z TradingView (Market Structure & Liquidity Heatmap),
- analizuje dane, porównuje poziomy cen ze wskaźników,
- automatycznie podejmuje decyzje tradingowe przez API Bybit (wejście i wyjście z pozycji).

🌐 Architektura:
- Backend: Python (Flask + pybit + dotenv + requests)
- Frontend: HTML / TailwindCSS / jsPDF
- Webhook: `https://ekoenergiadomowa.com/webhook_sqlite.php`
- API giełdy: Bybit REST API (via pybit)
- TradingView: alerty webhook JSON (Market Structure & Heatmap)
- Baza: SQLite (alerts.db)

📥 Wejścia:
1. **Alerty TradingView** w formacie JSON, z dwóch wskaźników:
   - Market Structure (`OrderBlock`, `entry`, `stoploss`, `direction`)
   - Liquidity Heatmap (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
2. Alerty zawierają dane:
   - symbol (np. BTCUSDT),
   - timestamp,
   - poziomy cen (`entry`, `sl`, `TP`, `levelHigh`, `levelLow`)

⚙️ Logika BOTa:
1. **Webhook odbiera alerty TradingView**.
2. Alert jest zapisywany do bazy SQLite (`alerts.db`).
3. Skrypt `fetch_from_sqlite.py` pobiera dane z bazy jako JSON API.
4. Alert analizowany i zapisywany do pamięci RAM.
5. BOT porównuje wartości:
   - Jeśli `entry` z MarketStructure pokrywa się z poziomem Liquidity (topGreen/bottomRed),
        - Jeżeli TOP_GREEN znajduje się w obszarze OrderBlock LONG -> BOT ustawia wejście zgodnie z zadanymi wartościami
        - Jeżeli BOTTOM_RED znajduje się w obszarze OrderBlock SHORT -> BOT ustawia wejście zgodnie z zadanymi wartościami
6. BOT podejmuje decyzję: **wejście w pozycję przez Bybit API**.
7. BOT oblicza lewar na podstawie % różnicy cen między wejściem a stop lossem.
8. BOT ustawia: wejście Limit, lewar, wysokość pozycji oraz Stop Loss i Target.
9. BOT **monitoruje cenę** przez API Bybit (co minutę).
10. Dla otwartej pozycji BOT nie reaguje dalej.
11. Oczekujące pozycje są anulowane, gdy poziomy Liquidity przestają być zgodne z OrderBlock.
12. Wszystko logowane (timestamp, symbol, wynik, transakcja).

🧾 Dodatkowo:
- Bot działa 24/7 w Google Cloud Run lub innej chmurze

📦 Zainstalowane biblioteki:
Python: `flask`, `requests`, `python-dotenv`, `pybit`, `reportlab`, `sqlite3`
Node: `axios`, `tailwindcss`, `jspdf`
Repo GitHub: `https://github.com/WiktoriaRebeka/Trading_Bot`

🛠 Status:
✅ Projekt zainicjowany w VS Code  
✅ Repo zsynchronizowane z GitHub  
✅ Wszystkie biblioteki zainstalowane  
🕓 Prace nad logiką BOTa trwają

### 🧩 webhook_sqlite.php
- Obsługuje `POST` z TradingView i zapisuje alerty do SQLite (`alerts.db`)
- Wyświetla ostatnie alerty jako HTML w GET

### 🔁 state_manager.py
- Bufory dla każdego symbolu:
  - 🟩 `TOP_GREEN_CHANGE`, 🟥 `BOTTOM_RED_CHANGE`: 5 ostatnich
  - 🟦 `OrderBlock`: 2 ostatnie
- Funkcje:
  - `update_alert(alert)` – dodaje alert do bufora
  - `get_last_heatmap(symbol, type)` – lista alertów Heatmapy
  - `get_last_orderblocks(symbol)` – lista OrderBlocków

### 🔄 fetch_from_sqlite.py
- Pobiera nowe alerty z endpointu PHP (`webhook_sqlite.php`)
- Zapisuje nowe rekordy do pliku `alerts_sqlite.jsonl`
- Wykrywa zmiany dzięki zapamiętanym ID alertów

### 🧠 process_alert(alert: dict)
- Waliduje alert, przypisuje do RAM (`state_manager`)
- Inicjalizuje symbol i grupuje według typu zdarzenia
- Debug: `state_manager.print_debug()`
