# Automated Trading Bot v5.1 (GCP)

## 1. Project Description and Main Goal

The **Automated Trading Bot v5.1** is a fully automated, serverless trading system running 24/7 on the Google Cloud Platform (GCP). Its primary objective is the autonomous execution and advanced analysis of a trading strategy based on the "Smart Money" concept, specifically targeting **Order Block (OB)** formations.

The system is designed for maximum reliability, analytical precision, and fault tolerance, while minimizing operational costs through the use of serverless technologies.

---

## 2. Operational Cycle and Core Business Logic

The system operates based on a precisely defined data and decision flow cycle:

### Stage 1: Signal Generation (TradingView)
- **Source:** A custom indicator written in Pine Script on the **TradingView** platform.
- **Indicator Logic:** The indicator analyzes the price chart in real-time for "Order Block" (`New OB`) formations.
- **Action:** Upon identifying a new, valid `New OB`, the indicator triggers an alert via a webhook.

### Stage 2: Alert Ingestion and Queuing (GCP)
- **Service:** **Cloud Function** (Python).
- **Flow:**
    1. A webhook from TradingView sends a POST request with the alert data in JSON format to the function's public URL.
    2. The Cloud Function immediately adds a server-side timestamp (`received_at`) to the alert and saves it as a new document in the dedicated `alerts` collection in **Cloud Firestore**. This collection acts as a reliable, persistent input queue.

### Stage 3: Cyclical Processing and Execution (GCP)
- **Orchestration:** **Cloud Scheduler** invokes the main bot service every minute (`* * * * *`).
- **Main Bot:** A **Cloud Run** service (a containerized Python/Flask application) that performs the following steps in each cycle:

#### 3.1. Setup Management (`active_setups`)
- The bot reads new alerts from the `alerts` queue in Firestore.
- Each new alert for a given symbol **overwrites** the previous "active setup" in the `active_setups` collection. This means a `New OB` invalidates an `Old OB` as a basis for opening *new* positions.

#### 3.2. Opening New Positions (`_handle_open_new_positions`)
- The bot iterates through all active setups.
- For each setup, it fetches the latest 1-minute candle (`kline`) data from the Bybit exchange API.
- **Entry Condition:** The decision to enter is based on an analysis of the candle's **`high` and `low` prices**, ensuring a precise detection of the `entry` level being "touched."
- **Entry Type:** The bot distinguishes whether it's the first entry on a given `New OB` (**`Fresh OB`**) or a subsequent one (**`Used OB`**).
- **Action:** When the conditions are met, the bot creates an **isolated document** in the `open_trades` collection, "freezing" all transaction parameters within it (SL, TP, entry price, `ob_type`, etc.).

#### 3.3. Monitoring Open Positions (`_handle_manage_open_trades`)
- The bot iterates through all documents in the `open_trades` collection. This loop is **completely independent** of the setups.
- For each open position, it checks if its "frozen" `sl` or `tp` has been breached, based on the `high/low` analysis of the latest candle.
- **Action (Closure):**
    1. A `WIN`/`LOSE` decision is made.
    2. A **historical `klines` analysis** is performed from the entry time to the present to precisely calculate the `rr_achieved`.
    3. A complete, final transaction record is written to the **BigQuery** analytical database.
    4. The trade document is deleted from `open_trades` and a new document is created in the `analyzed_trades` collection.

#### 3.4. Post-Mortem Analysis (`_handle_post_mortem_analysis`)
- The bot iterates through the "ghosts" of transactions in the `analyzed_trades` collection.
- It passively checks if the price has reached higher R:R levels (up to `tp_5_0`) or returned to the original `sl`.
- *Future work: This data will be used to periodically update the records in BigQuery.*
- Once `tp_5_0` or `sl` is reached, the document is deleted, ending the analysis lifecycle.

---

## 3. Technical Architecture

The system is built on best practices for serverless applications on GCP, with a focus on reliability, scalability, and security.

- **Data Ingestion:** `TradingView (Webhook)` -> `Cloud Function` -> `Cloud Firestore (collection 'alerts')`
- **Orchestration:** `Cloud Scheduler` (1-minute cron trigger).
- **Application Core:** `Cloud Run` (Docker container with a Python/Flask application).
- **State Management:**
    - **`active_setups`:** A Firestore collection. Stores the latest, active setup (New OB) for each symbol.
    - **`open_trades`:** A Firestore collection. Stores the state of each individual, open position, ensuring isolation and enabling concurrent trade monitoring.
    - **`analyzed_trades`:** A Firestore collection. Stores data of closed positions for passive analysis.
- **Logging and Analytics:**
    - **`Cloud Logging`:** The central hub for real-time monitoring and debugging of the bot's operations.
    - **`BigQuery`:** An analytical data warehouse used as a permanent archive for all closed trades. It serves for deep analysis of the strategy's effectiveness.
- **Network Infrastructure:**
    - **`Serverless VPC Access Connector` & `Cloud NAT`:** Provide a static, European IP address for all outbound traffic from Cloud Run, bypassing a geo-blocking issue with the Bybit exchange API.
- **Deployment Automation (CI/CD):**
    - **`GitHub Actions`:** Every `git push` to the `div` branch automatically triggers a workflow that builds a Docker image, pushes it to `Artifact Registry`, and deploys a new version of the service to Cloud Run.

---

## 4. Key Design Principles

- **State Isolation:** An open position is an independent entity. The arrival of a new setup must not affect its monitoring.
- **Data Precision:** All trading decisions (entry, SL, TP) are based on the analysis of a candle's `high` and `low`, not just a single, sampled price.
- **Fault Tolerance:** The application is written in a "defensive programming" style to handle incomplete data, API errors, and race conditions gracefully.
- **Multiple-Entry Strategy:** The system correctly implements the `Fresh OB` vs. `Used OB` logic, including the price reset condition after a `LOSE`, enabling advanced strategy analysis.

---

## 5. Project Structure and Key Files

The project is organized in a modular fashion to ensure readability and maintainability. The core components are located in the `/trading_bot/` directory.

-   **`main.py`**
    *   **Role:** The main entry point of the application (Flask framework).
    *   **Responsibility:** Handles incoming requests from Cloud Scheduler (`/run-bot-cycle`). Its sole purpose is orchestration – it invokes the logic for fetching new alerts and passes control to `bot_logic.py`. It also manages the lifecycle of setups in the `active_setups` collection.

-   **`bot_logic.py`**
    *   **Role:** The heart and brain of the entire system.
    *   **Responsibility:** Contains all the core business logic. It is divided into three main stages: opening new positions, monitoring open trades, and conducting passive post-mortem analysis. It communicates with the exchange's API and delegates state operations to the `state_manager.py`.

-   **`state_manager.py`**
    *   **Role:** A dedicated module for managing state in the Firestore database.
    *   **Responsibility:** Abstracts all Firestore operations. It contains functions to create, read, update, and delete documents in the three key collections: `active_setups`, `open_trades`, and `analyzed_trades`. This ensures that the rest of the code doesn't need to "know" the specifics of data storage.

-   **`bigquery_logger.py`**
    *   **Role:** The module responsible for communication with the analytical database.
    *   **Responsibility:** Defines the data schema (`EXPECTED_SCHEMA`) and provides functions for inserting new records (`INSERT`) and updating existing ones (`UPDATE`) in the `trades_history` table in BigQuery.

-   **`constants.py`**
    *   **Role:** A central place to store all constants and configuration parameters.
    *   **Responsibility:** Holds Firestore collection names, API URLs, BigQuery table names, and other "magic" values, making it easy to reconfigure the system without changing the core logic.

-   **`fetch_from_firestore.py`**
    *   **Role:** A specialized module for handling the input queue.
    *   **Responsibility:** Contains the logic for reading the last processed timestamp and fetching only those alerts from the `alerts` collection that have arrived since the last cycle.

-   **`firebase_client.py`**
    *   **Role:** A simple client for initializing the Firestore connection.
    *   **Responsibility:** Ensures that the database connection is established only once and provides a global client instance to the rest of the application.
