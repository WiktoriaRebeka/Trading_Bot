#Trading BOT using Liquiditiy Heatmap and Market Structure


🧠 Projekt: BOT Tradingowy oparty o alerty z TradingView

🔍 Cel:
Stworzenie BOTa działającego 24/7, który:
- odbiera alerty z TradingView (Market Structure & Liquidity Heatmap),
- analizuje dane, porównuje poziomy cen ze wskaźników,
- automatycznie podejmuje decyzje tradingowe przez API Bybit (wejście i wyjście z pozycji).

🌐 Architektura:
- Backend: Python (Flask + pybit + dotenv + requests)
- Frontend: HTML / TailwindCSS / jsPDF
- Webhook: `https://ekoenergiadomowa.com/webhook.php`
- API giełdy: Bybit REST API (via pybit)
- TradingView: alerty webhook JSON (Market Structure & Heatmap)

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
2. Alert jest zapisywany i analizowany.
3. BOT porównuje wartości:
   - Jeśli `entry` z MarketStructure pokrywa się z poziomem Liquidity (topGreen/bottomRed),
        - Jeżeli TOP_GREEN znajduje się w obszarze OrderBlock LONG -> BOT ustawia wejście zgodnie z zadanymi wartościami, wbudowanymi w alert MarketStructure (Stop Loss i Target podane w alercie)
        - Jeżeli BOTTOM_RED znajduje się w obszarze OrderBlock SHORT -> BOT ustawia wejście zgodnie z zadanymi wartościami, wbudowanymi w alert MarketStructure (Stop Loss i Target podane w alercie)
4. BOT podejmuje decyzję: **wejście w pozycję przez Bybit API**.
5. BOT oblicza lewar na podstawie % różnicy cen między wejściem a stop lossem (obliczenia w dalszej części)
6. BOT wchodzi na ByBit i ustawia: wejście Limit, lewar, wysokość pozycji oraz Stop Loss i Target
7. BOT **monitoruje cenę** przez API Bybit (co minutę):
8. Dla otwartej pozycji BOT nie reaguje dalej, pozycja zostaje zamknięta przez Stop Loss lub przez Target, na ByBit, BOT nie ingeruje więcej w pozycję która została otwarta
9. W przypadku pozycji która oczekuje, BOT ma obowiązek usunięcia pozycji, gdy:
    - TOP_GREEN znajdzie się poza obszarem OrderBlock LONG (powyżej ceny entry lub poniżej ceny Stop Loss)
    - BOTTOM-RED znajdzie się poza obszarem OrderBlock SHORT (powyżej ceny Stop Loss lub poniżej ceny entry)
10. Wszystko logowane (timestamp, symbol, wynik, wynik transakcji).

🧾 Dodatkowo:
- Bot działa 24/7 w Google Cloud Run lub innej chmurze

📦 Zainstalowane biblioteki:
Python: `flask`, `requests`, `python-dotenv`, `pybit`, `reportlab`  
Node: `axios`, `tailwindcss`, `jspdf`  
Repo GitHub: `https://github.com/WiktoriaRebeka/Trading_Bot`

🛠 Status:
✅ Projekt zainicjowany w VS Code  
✅ Repo zsynchronizowane z GitHub  
✅ Wszystkie biblioteki zainstalowane  
🕓 Prace nad logiką BOTa trwają

### 🧩 webhook.py
Obsługuje webhooki z TradingView i zapisuje alerty do pliku:

- `POST /webhook` – zapisuje alert JSON do `alerts_log.jsonl`
- `GET /webhook` – wyświetla ostatnie 50 alertów w przeglądarce
- Działa lokalnie na `localhost:8000` lub `0.0.0.0:8000`
- Format: JSON alert + znacznik czasu `received_at`


### 🔁 state_manager.py
Zarządza przechowywaniem ostatnich alertów z TradingView w pamięci (RAM), oddzielnie dla każdego symbolu (np. AVAXUSDT.P):

- 🟩 `TOP_GREEN_CHANGE` i 🟥 `BOTTOM_RED_CHANGE`: 5 ostatnich
- 🟦 `OrderBlock` (MarketStructure): 2 ostatnie
- Bufory są automatycznie nadpisywane
- Funkcje:
  - `update_alert(alert)` – dodaje alert do bufora
  - `get_last_heatmap(symbol, type)` – zwraca listę ostatnich alertów Heatmapy
  - `get_last_orderblocks(symbol)` – zwraca 2 ostatnie orderblocki
