# Trading BOT using Liquidity Heatmap and Market Structure

🧠 **Project**: Automated Trading Bot using TradingView Alerts

🔍 **Goal**:
Develop a 24/7 trading bot that:
- Receives alerts from TradingView (Market Structure & Liquidity Heatmap),
- Analyzes price levels from those indicators,
- Automatically opens/closes positions via Bybit API (entry, stop-loss, take-profit).

🌐 **Architecture**:
- **Backend**: Python (Flask, pybit, dotenv, requests, sqlite3)
- **Frontend**: HTML / TailwindCSS / jsPDF
- **Webhook**: `https://ekoenergiadomowa.com/webhook_sqlite.php`
- **Exchange API**: Bybit REST API (via pybit)
- **Alert Source**: TradingView JSON webhook alerts
- **Database**: SQLite (`alerts.db`)

📅 **Input**:
1. **TradingView Alerts** in JSON format from two indicators:
   - Market Structure (`OrderBlock`, `entry`, `stoploss`, `direction`)
   - Liquidity Heatmap (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
2. Alert data includes:
   - `symbol` (e.g. BTCUSDT),
   - `timestamp` (Europe/Warsaw time zone),
   - price levels: `entry`, `sl`, `TP`, `levelHigh`, `levelLow`

⚙️ **Bot Logic**:
1. TradingView sends JSON alert to a PHP webhook.
2. The webhook `webhook_sqlite.php` logs it into SQLite `alerts.db`.
3. The `fetch_from_sqlite.py` script fetches new alerts every 60 seconds from the PHP endpoint.
4. Each alert is processed via `process_alert()` and stored in RAM (`state_manager.py`).
5. The bot compares:
   - If OrderBlock `entry` aligns with topGreen / bottomRed zones:
     - **LONG**: if `TOP_GREEN` is within OrderBlock range
     - **SHORT**: if `BOTTOM_RED` is within OrderBlock range
6. The bot places Limit Order + Stop Loss + Take Profit with calculated leverage.
7. It monitors live price action for active positions.
8. It cancels plans if price conditions invalidate.
9. Logs all operations to the terminal (for now, archiving planned).

📟 **Additionally**:
- Bot runs 24/7 (can be deployed in Google Cloud Run or other cloud infra)

📦 **Libraries**:
Python: `flask`, `requests`, `python-dotenv`, `pybit`, `reportlab`, `sqlite3`
Node: `axios`, `tailwindcss`, `jspdf`
GitHub: `https://github.com/WiktoriaRebeka/Trading_Bot`

🛠 **Project Status**:
✅ Project started
✅ Repo is active
✅ SQLite database working
🧠 Integrating and analyzing alerts in progress

---

### 🧹 `webhook_sqlite.php`
- Handles `POST` (TradingView) → saves alert to `alerts.db`
- Handles `GET` (HTML and JSON view)
- Converts `timestamp` to `Europe/Warsaw`

### 🔁 `state_manager.py`
- RAM buffers:
  - Heatmap: 5 alerts (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
  - MarketStructure: 2 `OrderBlock` alerts
- Functions:
  - `update_alert(alert)` → buffering
  - `print_debug()` → diagnostics

### 🔄 `fetch_from_sqlite.py`
- Reads JSON from `webhook_sqlite.php?format=json`
- Detects new alerts by `id`
- Writes them to `alerts_sqlite.jsonl`
- Passes each alert to `process_alert()`

### 💡 `bot_logic.py`
- Checks entry conditions for LONG and SHORT
- Matches OrderBlock with Heatmap levels
- Gets real-time price from Bybit
- Handles statuses: `planned`, `opened`, `cancelled`, `closed`

### 📃 `positions_logger.py`
- Logs all positions to `positions_log.jsonl`
- Tracks planning, opening, closing, and result (`WIN` / `LOST`)

### 📊 `main.py`
- Runs `fetch_from_sqlite.py` in a background thread
- Every 15s, analyzes conditions and makes trading decisions
