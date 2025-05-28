# Trading BOT using Liquidity Heatmap and Market Structure

🧠 **Project**: Automated Trading Bot using TradingView Alerts

🔍 **Goal**:
Develop a 24/7 trading bot that:
- Receives alerts from TradingView (Market Structure & Liquidity Heatmap),
- Analyzes price levels from those indicators,
- Automatically opens/closes positions via Bybit API (entry, stop-loss, take-profit).

🌐 **Architecture**:
- **Backend**: Python (Flask, pybit, dotenv, requests, PostgreSQL via Supabase)
- **Frontend**: HTML / TailwindCSS / jsPDF
- **Webhook**: `https://your-render-url.onrender.com/webhook`
- **Exchange API**: Bybit REST API (via pybit)
- **Alert Source**: TradingView JSON webhook alerts
- **Database**: PostgreSQL (Supabase)

📅 **Input**:
1. **TradingView Alerts** in JSON format from two indicators:
   - Market Structure (`OrderBlock`, `entry`, `stoploss`, `direction`)
   - Liquidity Heatmap (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
2. Alert data includes:
   - `symbol` (e.g. BTCUSDT),
   - `timestamp` (ISO 8601),
   - price levels: `entry`, `sl`, `TP`, `levelHigh`, `levelLow`

⚙️ **Bot Logic**:
1. TradingView sends JSON alert to a **Flask webhook** hosted on Render.
2. The webhook logs the alert directly to **Supabase PostgreSQL**.
3. The `fetch_from_sqlite.py` script (or Supabase client) fetches new alerts regularly.
4. Each alert is processed via `process_alert()` and stored in RAM (`state_manager.py`).
5. The bot compares:
   - If OrderBlock `entry` aligns with topGreen / bottomRed zones:
     - **LONG**: if `TOP_GREEN` is within OrderBlock range
     - **SHORT**: if `BOTTOM_RED` is within OrderBlock range
6. The bot places Limit Order + Stop Loss + Take Profit with calculated leverage.
7. It monitors live price action for active positions.
8. It cancels planned entries if price conditions are no longer valid.
9. Logs all operations to the terminal and `positions_log.jsonl`.

📟 **Additionally**:
- Bot runs 24/7 (deployed on Render, optionally on VPS or GCP)

📦 **Libraries**:
Python:  
- `flask`  
- `requests`  
- `python-dotenv`  
- `psycopg2-binary`  
- `pybit`  
- `reportlab`

Node:  
- `axios`  
- `tailwindcss`  
- `jspdf`

📁 **Repository**:  
GitHub: [`https://github.com/WiktoriaRebeka/Trading_Bot`](https://github.com/WiktoriaRebeka/Trading_Bot)

🛠 **Project Status**:
✅ Project started  
✅ Repo is active  
✅ Supabase + Flask webhook working  
✅ Alert processing logic working  
✅ Realtime entry check integrated

---

### 🛰️ `app/webhook.py`
- Flask server (Render deployment)
- Handles `POST` (TradingView JSON alert) → saves directly to Supabase
- Handles `GET` → returns last 100 alerts in JSON format

### 🔁 `state_manager.py`
- RAM buffers per `symbol`
  - Heatmap: 5 latest (`TOP_GREEN_CHANGE`, `BOTTOM_RED_CHANGE`)
  - OrderBlock: 2 latest entries
- Handles:
  - `update_alert(alert)` → updates buffers
  - `get_last_heatmap(symbol, type)`  
  - `get_last_orderblocks(symbol)`  
  - `print_debug()`  

### 🔄 `fetch_from_sqlite.py` *(legacy fallback)*
- Previously fetched alerts from local Flask SQLite endpoint
- Can be modified to fetch from Supabase via REST or Python client

### 💡 `bot_logic.py`
- Core decision logic:
  - Matches Market Structure with Liquidity Levels
  - Verifies `entry`, `stoploss`, `target`
  - Uses real-time Bybit price via REST API
  - Validates if alert is "recent" (max 120s old)
  - Logs planned positions

### 📃 `positions_logger.py`
- Logs each position to `positions_log.jsonl`
- Tracks statuses: `planned`, `opened`, `cancelled`, `closed`
- Adds timestamps and optional `result` field

### 📊 `main.py`
- Starts alert fetcher (via threading)
- Every 15 seconds:
  - Loops through active `symbols`
  - Evaluates logic for long/short entry
  - Logs and prints results

---

📝 **Deploy URL**:  
TradingView Webhook: `https://trading-bot-webhook-xxxx.onrender.com/webhook`

📤 **To send alerts**:
1. In TradingView → Create Alert
2. Paste your webhook URL
3. Use **JSON body** like:
```json
{
  "symbol": "BTCUSDT",
  "type": "OrderBlock",
  "entry": 12345.67,
  "stoploss": 12200.00,
  "TP": 12700.00,
  "direction": "LONG",
  "timestamp": "2025-05-28T19:42:00Z"
}
