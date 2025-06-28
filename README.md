# Automated Trading Bot v6.5 (GCP)

## 1. Project Description and Main Goal

The **Automated Trading Bot v6.5** is a fully automated, serverless trading system running 24/7 on the Google Cloud Platform (GCP). Its primary objective is the autonomous execution and advanced analysis of a trading strategy based on the "Smart Money" concept, specifically targeting **Order Block (OB)** formations.

The system is designed for maximum reliability and fault tolerance by decoupling data collection from the core trading logic. It aims to minimize operational costs and ensure analytical precision through a robust, multi-component serverless architecture.

---

## 2. Operational Cycle and Core Business Logic

The system operates based on a precisely defined, multi-stage data and decision flow:

### Stage 1: Signal Generation (TradingView)
- **Source:** A custom Pine Script indicator on the **TradingView** platform.
- **Indicator Logic:** The indicator analyzes the price chart in real-time for `New OB` (Order Block) formations.
- **Action:** Upon identifying a new, valid `New OB`, the indicator triggers an alert via a webhook.

### Stage 2: Alert Ingestion and Queuing (GCP)
- **Service:** A lightweight **Cloud Function** acting as a webhook endpoint.
- **Flow:**
    1. The webhook from TradingView sends a POST request with the alert data (JSON) to the function's public URL.
    2. The function immediately saves the raw alert as a new document in the `alerts` collection in **Cloud Firestore**, which serves as a persistent input queue.

### Stage 3: Data Collection (Independent Microservice)
To ensure reliability and mitigate API latency issues, a dedicated data collection service runs independently.
- **Service:** A **Cloud Function** (`data-collector-func`) triggered every minute by **Cloud Scheduler**.
- **Flow:**
    1. The function fetches a predefined list of symbols to monitor (`SYMBOLS_TO_WATCH`).
    2. It queries the Bybit API for the **latest 1-minute `kline` data** for each symbol, using a built-in retry mechanism to handle transient network errors.
    3. It saves the `high`, `low`, and `close` prices for each symbol into a dedicated Firestore collection: `latest_klines`. This collection acts as a fast, reliable, internal data cache.

### Stage 4: Cyclical Processing and Execution (Main Bot)
- **Orchestration:** A separate **Cloud Scheduler** job invokes the main bot service every minute.
- **Main Bot:** A **Cloud Run** service (containerized Python) that performs the following steps:

#### 4.1. Setup Management
- The bot reads new alerts from the `alerts` queue.
- Each new alert for a given symbol **overwrites** the previous "active setup" in the `active_setups` collection. This invalidates the `Old OB` as a basis for opening *new* positions.

#### 4.2. Opening New Positions
- The bot **reads the cached price data** from its own `latest_klines` collection in Firestore, instead of calling the Bybit API directly.
- It iterates through all active setups and, based on the `high` and `low` of the latest candle, decides whether to open a new position.
- It correctly identifies **`Fresh OB`** (first entry) vs. **`Used OB`** (subsequent entries) and handles the price reset logic after a `LOSE`.
- Upon entry, it creates an **isolated document** in the `open_trades` collection, "freezing" all transaction parameters (SL, TP, etc.).

#### 4.3. Monitoring Open Positions
- The bot iterates through all documents in the `open_trades` collection, which is **completely independent** of the setups.
- It uses the cached `kline` data from `latest_klines` to check if any position's "frozen" `sl` or `tp` has been breached.
- **Action (Closure):** When a position is closed, a **historical `klines` analysis** is performed to calculate the final `rr_achieved`, a complete record is sent to **BigQuery**, and the state is updated.

#### 4.4. Post-Mortem Analysis
- After a trade is closed, a "ghost" of it is created in the `analyzed_trades` collection.
- The bot passively tracks this ghost against live prices to update its final potential R:R in BigQuery, until `tp_5_0` or the original `sl` is hit.

---

## 3. Technical Architecture

The system employs a decoupled, microservice-oriented architecture on GCP.

- **Data Ingestion:** `TradingView (Webhook)` -> `Cloud Function` -> `Cloud Firestore (collection 'alerts')`
- **Data Collection:** `Cloud Scheduler` -> `Cloud Function (data-collector-func)` -> `Bybit API` -> `Cloud Firestore (collection 'latest_klines')`
- **Orchestration:** `Cloud Scheduler` (1-minute cron trigger for the main bot).
- **Application Core:** `Cloud Run` (Docker container with the main Python/Flask application).
- **State Management (Firestore):**
    - **`active_setups`:** Stores the latest, active setup (New OB) for each symbol.
    - **`open_trades`:** Stores the state of each individual, open position.
    - **`analyzed_trades`:** Stores data of closed positions for passive post-mortem analysis.
- **Analytics & Logging:**
    - **`Cloud Logging`:** Central hub for real-time monitoring and debugging.
    - **`BigQuery`:** Analytical data warehouse serving as a permanent archive for all closed trades.
- **Network Infrastructure:**
    - **`Serverless VPC Access Connector` & `Cloud NAT`:** Provide a static, European IP address for all outbound traffic, bypassing geo-blocking issues with the exchange API.
- **Deployment Automation (CI/CD):**
    - **`GitHub Actions`:** Every `git push` to the `div` branch triggers a workflow to build, push, and deploy new versions of the services.

---

## 4. Key Design Principles

- **Decoupling:** Data collection is separated from trading logic, making the main bot faster and more resilient to external API failures.
- **State Isolation:** An open position is an independent entity. The arrival of a new setup does not affect its monitoring.
- **Data Precision:** All trading decisions are based on the analysis of a candle's `high` and `low`, not a single, sampled price.
- **Fault Tolerance:** The application is written defensively to handle incomplete data, API errors, and race conditions.
- **Advanced Strategy Logic:** The system correctly implements the `Fresh OB` vs. `Used OB` logic, including the price reset condition after a `LOSE`.

---

## 5. Project Structure and Key Files

The project is organized modularly within the `/trading_bot/` directory.

-   **`main.py`**: The main entry point (Flask) for the **trading bot service**. It orchestrates the cycle by fetching new alerts and invoking the core logic.
-   **`bot_logic.py`**: The brain of the system. Contains the core business logic for opening, managing, and analyzing trades based on data from Firestore.
-   **`data_collector.py`**: The code for the **data collector microservice** (deployed as a separate Cloud Function). Its sole responsibility is to fetch `kline` data from the exchange and cache it in Firestore.
-   **`state_manager.py`**: A dedicated module for abstracting all state management operations on the three key Firestore collections (`active_setups`, `open_trades`, `analyzed_trades`).
-   **`bigquery_logger.py`**: The module responsible for all communication with BigQuery, including `INSERT` and `UPDATE` operations.
-   **`constants.py`**: A central place for all constants and configuration parameters (collection names, API URLs, etc.).
-   **`fetch_from_firestore.py`**: A specialized module for handling the `alerts` input queue.
-   **`firebase_client.py`**: A simple client for initializing a global Firestore connection.