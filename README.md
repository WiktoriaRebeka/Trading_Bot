# Trading BOT using Liquidity Heatmap and Market Structure

🧠 **Project**: Automated Trading Bot using TradingView Alerts

🔍 **Goal**:
Develop a 24/7 trading bot that:
- Receives alerts from TradingView (Market Structure & Liquidity Heatmap),
- Analyzes price levels from those indicators,
- Automatically opens/closes positions via Bybit API (entry, stop-loss, take-profit) - *future functionality*.

🌐 **Architecture**:
- **Backend**: Python (Flask, firebase-admin, python-dotenv, requests)
- **Webhook**: Flask application deployed on Render.com (`https://trading-bot-webhook-pdh3.onrender.com/webhook` - *aktualny URL*)
- **Exchange API for Price Tickers**: Bybit REST API V5 (public endpoint)
- **Alert Source**: TradingView JSON webhook alerts
- **Database for Alerts**: Cloud Firestore (Firebase)

📅 **Input**:
1.  **TradingView Alerts** in JSON format from two indicators:
    *   Market Structure (generates `OrderBlock` type alerts)
    *   Liquidity Heatmap (generates `TOP_GREEN_CHANGE` or `BOTTOM_RED_CHANGE` event alerts)
2.  Alert data typically includes:
    *   `symbol` (e.g., BTCUSDT, SUIUSDT.P) or `ticker`
    *   `timestamp` (ISO 8601 UTC - original alert time from TradingView)
    *   `type` or `event` (e.g., "OrderBlock", "TOP_GREEN_CHANGE")
    *   Price levels for OrderBlocks: `entry`, `sl`, `tp`, `levelHigh`, `levelLow`, `direction`
    *   Price level for LiquidityHeatmap: `value`

⚙️ **Bot Logic & Data Flow**:
1.  TradingView sends a JSON alert via `POST` request to a **Flask webhook** hosted on Render.com.
2.  The webhook (`app/webhook.py`):
    *   Authenticates with Firebase using Admin SDK and a service account key (managed as a Secret File on Render).
    *   Adds a `received_at` server timestamp (Firestore Timestamp type).
    *   Converts the `timestamp` from TradingView (if present and valid ISO string) to Firestore Timestamp type.
    *   Saves the alert as a new document in the **`alerts` collection in Cloud Firestore**.
3.  A locally running Python bot (`app/main.py`):
    *   A dedicated thread (`app/fetch_from_firestore.py`) regularly queries Cloud Firestore for new alerts (since the last processed `received_at` timestamp). It authenticates using Firebase Admin SDK and a local service account key.
    *   Each new alert is processed by `app/state_manager.py` and its relevant details are stored in RAM (in-memory deques) per symbol.
    *   The main bot loop (`app/bot_logic.py` via `app/main.py`):
        *   Compares the latest OrderBlock alert with the latest relevant LiquidityHeatmap alert for each symbol.
        *   **LONG Entry Condition**: Fresh `OrderBlock` (LONG) + fresh `LiquidityHeatmap` (TOP\_GREEN\_CHANGE) where heatmap `value` is between OrderBlock's `sl` and `entry`.
        *   **SHORT Entry Condition**: Fresh `OrderBlock` (SHORT) + fresh `LiquidityHeatmap` (BOTTOM\_RED\_CHANGE) where heatmap `value` is between OrderBlock's `entry` and `sl`.
        *   If conditions are met, a position is "planned".
        *   *(Future: Bot will place Limit Order + SL/TP on Bybit via API).*
        *   Monitors live price action (via Bybit public API) for planned positions to decide on opening, and for open positions to decide on closing (SL/TP hit).
        *   Cancels planned entries if market conditions invalidate the setup.
4.  All significant operations and position status changes (`planned`, `opened`, `cancelled`, `closed`) are logged to the terminal and to a local file `positions_log.jsonl` (`app/positions_logger.py`).

📟 **Additionally**:
- The local bot is intended to run 24/7 on a local machine (Windows).

📦 **Key Python Libraries**:
- `Flask`: For the webhook server.
- `firebase-admin`: For interacting with Cloud Firestore.
- `requests`: For making HTTP requests (e.g., to Bybit API).
- `python-dotenv`: For managing environment variables.
- `gunicorn`: Used by Render.com to serve the Flask application.

*(Frontend (HTML/Tailwind/jsPDF) and Node-related libraries mentioned in the old README are not currently part of the core bot/webhook backend described).*

📁 **Repository**:
GitHub: [`https://github.com/WiktoriaRebeka/Trading_Bot`](https://github.com/WiktoriaRebeka/Trading_Bot)

🛠 **Project Status (as of Firestore Migration & Webhook Test)**:
✅ Project started, initial version with Supabase developed.
✅ Migrated alert storage backend from Supabase to Cloud Firestore.
✅ Flask webhook (`app/webhook.py`) successfully deployed on Render.com.
    ✅ Webhook receives test POST requests.
    ✅ Webhook authenticates with Firebase Admin SDK using a service account key.
    ✅ Webhook successfully writes alert data to Cloud Firestore, including `received_at` and converted `timestamp` fields.
✅ Local bot (`app/main.py` & `app/fetch_from_firestore.py`):
    ✅ Successfully initializes Firebase Admin SDK using a local service account key.
    ✅ Successfully fetches new alerts from Cloud Firestore.
✅ Core alert processing logic (`app/state_manager.py`, `app/bot_logic.py`) in place.
✅ Position logging to `positions_log.jsonl` implemented.
⏳ **Current Focus:** Testing end-to-end flow with live TradingView alerts, verifying bot's decision-making logic, and debugging price fetching from Bybit API (symbol format issues).

---

### 🛰️ `app/webhook.py`
- Flask server deployed on Render.com.
- Handles `POST` requests from TradingView (JSON alert) → authenticates with Firebase, adds `received_at` timestamp, converts `timestamp` field, and saves the alert to Cloud Firestore's `alerts` collection.
- Responds with `HTTP 405 Method Not Allowed` for `GET` requests to `/webhook` (as expected).

### 🔁 `app/state_manager.py`
- Manages in-memory (RAM) buffers for recent alerts, per `symbol`:
  - LiquidityHeatmap: Stores the latest N alerts (e.g., 5) for `TOP_GREEN_CHANGE` and `BOTTOM_RED_CHANGE` events.
  - OrderBlock: Stores the latest M alerts (e.g., 2).
- Provides functions to retrieve the latest alerts for `bot_logic.py`.
- Manages in-memory state for `planned` and `opened` positions.

### 🔄 `app/fetch_from_firestore.py`
- Runs in a separate thread in the local bot.
- Periodically queries the `alerts` collection in Cloud Firestore for new documents based on the `received_at` field (since the last processed timestamp).
- Authenticates using Firebase Admin SDK and a local service account key.
- Passes new alerts to `state_manager.process_alert()`.

### 💡 `app/bot_logic.py`
- Contains the core decision-making logic.
- `get_current_price(raw_tv_symbol)`: Fetches current market price from Bybit API V5, includes logic (`get_bybit_compatible_symbol`) to convert TradingView symbol format (e.g., `SUIUSDT.P`) to Bybit API compatible format (e.g., `SUIUSDT`).
- `is_alert_recent()`: Checks if an alert is within a defined freshness window (e.g., 120 seconds).
- `_check_and_plan_position()`: Evaluates conditions for LONG/SHORT entries based on OrderBlock and LiquidityHeatmap data from `state_manager`.
- `monitor_planned_positions()`: Checks if planned positions can be opened or should be cancelled.
- `monitor_opened_positions()`: Checks if open positions hit SL/TP.

### 📃 `app/positions_logger.py`
- Logs details of each position (ID, symbol, direction, entry, SL, TP, etc.) and its status changes (`planned`, `opened`, `cancelled`, `closed`) to a local JSONL file (`positions_log.jsonl`).
- Includes timestamps for each status change.

### 📊 `app/main.py`
- Main entry point for the local bot.
- Initializes and starts the `fetch_from_firestore` thread.
- Runs the main bot loop, which periodically (e.g., every 15 seconds):
  - Iterates through symbols активных in `state_manager`.
  - Calls functions from `bot_logic.py` to check for new entries and monitor existing positions.
- Handles `KeyboardInterrupt` for graceful shutdown.

---

📝 **Deploy URL**:
TradingView Webhook: `https://trading-bot-webhook-pdh3.onrender.com/webhook` *(Actual and tested URL)*

📤 **To send alerts (Example TradingView JSON body for OrderBlock)**:
1.  In TradingView → Create Alert.
2.  Webhook URL: `https://trading-bot-webhook-pdh3.onrender.com/webhook`
3.  Message (JSON body):
```json
{
  "source": "MarketStructure",
  "type": "OrderBlock",
  "symbol": "BTCUSDT.P", 
  "direction": "LONG",
  "entry": {{plot("EntryPrice")}},
  "sl": {{plot("StopLoss")}},
  "tp": {{plot("TakeProfit")}},
  "levelHigh": {{plot("LevelHigh")}},
  "levelLow": {{plot("LevelLow")}},
  "timestamp": "{{time}}"
}