# Trading BOT using Liquidity Heatmap and Market Structure (Deployed on Google App Engine)

🧠 **Project**: Automated Trading Bot using TradingView Alerts, with backend logicบน Google App Engine and data storage in Cloud Firestore.

🔍 **Goal**:
Develop a 24/7 trading bot that:
1.  Receives alerts from TradingView (Market Structure & Liquidity Heatmap) via a webhook on Render.com.
2.  Stores these alerts persistently in Cloud Firestore.
3.  Analyzes price levels and alert combinations to identify potential trading setups.
4.  Manages the lifecycle of positions (planned, opened, cancelled, closed) in Firestore.
5.  (Future) Automatically opens/closes positions via Bybit API (entry, stop-loss, take-profit). Currently, it simulates these actions and fetches live prices.

🌐 **Core Architecture & Data Flow**:

1.  **TradingView (Alert Source)**:
    *   Pine Script indicators generate alerts for "OrderBlock" and "LiquidityHeatmap" events.
    *   Alerts are sent as JSON via HTTP POST to a webhook.

2.  **Render.com (Webhook Receiver)**:
    *   A Python/Flask application (`app/webhook.py` - *code for this component is separate from the App Engine bot logic*) deployed at `https://trading-bot-webhook-pdh3.onrender.com/webhook`.
    *   Receives alerts, adds a `received_at` server timestamp, and saves the alert data to the `alerts` collection in **Cloud Firestore**. *(This part is considered operational)*.

3.  **Google Cloud Firestore (Persistent Data Store)**:
    *   `alerts` collection: Stores all incoming alerts from TradingView (via Render).
    *   `bot_config` collection: Stores bot's operational state, e.g., `last_fetch_state` document with `last_processed_firestore_timestamp`.
    *   `planned_positions` collection: Stores details of setups identified by the bot, awaiting market conditions for entry.
    *   `opened_positions` collection: Stores details of positions that have been "opened" (entry conditions met).
    *   `trading_positions` collection: Serves as an archive/log of all position lifecycle events (planned, opened, cancelled, closed) with outcomes.

4.  **Google App Engine (Core Bot Logic - `tradingbotdatabase-c544d` project)**:
    *   A Python/Flask application (`app/main.py`) served by Gunicorn, running in the Standard Environment.
    *   **Initialization (`app/main.py` & `app/firebase_client.py`):**
        *   On startup, initializes the Firebase Admin SDK using default App Engine service account credentials.
        *   Provides a shared Firestore client instance (`db_client`) via `get_db()`.
    *   **Main Bot Cycle Endpoint (`/run-bot-cycle` in `app/main.py`):**
        *   Designed to be triggered acessórios by **Cloud Scheduler**.
        *   **Fetches Data (`app/fetch_from_firestore.py`):** Queries Firestore for new alerts since the last processed timestamp.
        *   **Manages Alert State (`app/state_manager.py`):**
            *   Processes new alerts and stores recent, relevant alert data (latest OrderBlocks, Heatmap changes per symbol) in-memory (RAM `deque`s) for quick access during decision-making.
            *   Manages CRUD operations for `planned_positions` and `opened_positions` in Firestore, utilizing Firestore transactions for atomicity.
        *   **Executes Trading Logic (`app/bot_logic.py`):**
            *   Retrieves current market prices for active symbols from **Bybit API V5** (Market Tickers endpoint).
            *   `check_for_new_setups(symbol)`:
                *   Compares the latest OrderBlock with the latest relevant LiquidityHeatmap alert (from in-memory state).
                *   **LONG Entry Condition**: Fresh `OrderBlock` (LONG) + fresh `LiquidityHeatmap` (TOP\_GREEN\_CHANGE) where heatmap `value` is between OrderBlock's `sl` and `entry`.
                *   **SHORT Entry Condition**: Fresh `OrderBlock` (SHORT) + fresh `LiquidityHeatmap` (BOTTOM\_RED\_CHANGE) where heatmap `value` is between OrderBlock's `entry` and `sl`.
                *   If conditions met and a similar setup (based on the same `triggering_ob_timestamp`) isn't already planned, a new document is created in the `planned_positions` Firestore collection.
            *   `monitor_positions(all_current_prices)`:
                *   Iterates through positions in `planned_positions` (from Firestore):
                    *   `check_cancellation_conditions()`: Evaluates if a planned position should be cancelled (e.g., OrderBlock invalidated by a newer, opposing OB; heatmap level out of original OB zone). If so, removes from `planned_positions` and logs to `trading_positions` as "cancelled".
                    *   `check_and_process_planned_position()`: If not cancelled, checks if `current_price` has reached `entry_price`. If so, moves the position atomically from `planned_positions` to `opened_positions` in Firestore and logs to `trading_positions` as "opened".
                *   Iterates through positions in `opened_positions` (from Firestore):
                    *   `check_and_process_opened_position()`: Checks if `current_price` has hit `stop_loss` or `take_profit`. If so, removes from `opened_positions` atomically and logs to `trading_positions` as "closed" (with reason SL/TP_HIT).
        *   **Logs Position History (`app/positions_logger.py`):** Records all significant position lifecycle events and status changes to the `trading_positions` collection in Firestore.
    *   **HTTP Endpoints:**
        *   `/`: Health check, indicates if the app is running and Firebase is initialized.
        *   `/_ah/warmup`: Standard App Engine warmup handler.

📅 **Input Data Structure (Typical JSON from TradingView):**
```json
{
  "source": "MarketStructure", // or "LiquidityHeatmap"
  "type": "OrderBlock", // or "TOP_GREEN_CHANGE", "BOTTOM_RED_CHANGE"
  "symbol": "BTCUSDT.P", 
  "direction": "LONG", // Only for OrderBlock
  "entry": 68000.50,
  "sl": 67800.00,
  "tp": 69000.00,
  "levelHigh": 68050.00, // Upper bound of OB zone
  "levelLow": 67950.00,  // Lower bound of OB zone
  "timestamp": "2025-06-12T10:00:00Z", // ISO 8601 UTC
  "value": null // or a price level for Heatmap alerts
}


📦 Key Python Libraries (for App Engine Bot):
Flask: For HTTP endpoints.
gunicorn: WSGI server for Flask on App Engine.
firebase-admin: Interacting with Cloud Firestore.
requests: Making HTTP requests to Bybit API.
python-dotenv: For local environment variable management (not used directly on App Engine).
logging: Standard Python logging.


TRADING_BOT/
├── .env                    # Local environment variables (in .gitignore)
├── .gcloudignore             # Files to ignore for App Engine deployment
├── .gitignore                # Files to ignore for Git
├── app.yaml                  # App Engine configuration (incl. env variables)
├── firebase_key.json         # (Potentially) Service account key for local Firebase access
├── hello_main.py             # (Potentially) A simple test file, not part of the core app
├── package.json              # Node.js dependencies manifest (if any JS tools are used)
├── package-lock.json         # Node.js lock file
├── README.md                 # This file
├── requirements.txt          # Python dependencies
│
├── app/                      # Main application source code
│   ├── __init__.py
│   ├── bot_logic.py          # Core trading decision logic, price fetching
│   ├── constants.py            # Application constants, config loading
│   ├── fetch_from_firestore.py # Logic for fetching alerts & managing timestamps
│   ├── firebase_client.py      # Firebase Admin SDK initialization
│   ├── main.py                 # Flask app, Gunicorn entrypoint, main coordinator
│   ├── positions_logger.py     # Logging position lifecycle to Firestore
│   └── state_manager.py        # In-memory alert cache & strategy state management
│
└── node_modules/             # Node.js installed packages


