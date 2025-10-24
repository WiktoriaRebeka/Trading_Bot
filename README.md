## Key Concepts

This system is built on a few fundamental principles to ensure data integrity and resilience on a defined schedule.
6.  **Firestore:** The operational NoSQL database used for storing alerts, active order data.

-   **`orderLinkId` as the Golden Thread**: We generate our own unique, immutable ID for every trade operation, called `orderLinkId`. This ID is sent with the initial order to Bybit and serves as the single,, and system state.
7.  **BigQuery:** The analytical data warehouse where final, immutable trade history records are stored for performance analysis.
8.  **Secret Manager:** Securely stores sensitive information like API keys.

## Core Concepts

The system is built on a few fundamental principles:

*   **`orderLinkId` as the Golden Thread:** We generate reliable link between a TradingView alert, the opening order, and the closing trade, even when Bybit assigns different ` a unique, custom `orderLinkId` for every trade operation. This ID is the single source of truth that links theorderId`s for TP/SL events.

-   **`active_orders` as the Golden Record**: The initial alert, the opening order on the exchange, and the final closing trade, even though the exchange assigns different `orderId`s for `active_orders` collection in Firestore is the system's single source of truth. Each document, identified by its `orderLinkId`, opening and closing orders.
*   **Separation of Concerns:** The trading logic is split into three distinct, asynchronous processes tracks the entire lifecycle of a trade—from its planned parameters to the exchange-assigned IDs for its TP/SL legs, each with a single responsibility. This makes the system more resilient and easier to debug.
    1.  **Pl.

-   **Proactive Data Enrichment**: Instead of trying to find trade details after the fact, the system uses a dedicated processacing Orders:** Handled exclusively by `/process-alerts`.
    2.  **Monitoring Open Orders:** Handled by `/ (`/update-orders`) to monitor newly filled orders and proactively enrich the `active_orders` record with the `update-orders`.
    3.  **Logging Closed Trades:** Handled by `/log-pnl`.
*   **Resilience and Idempotency:** The system is designed to handle failures and retries gracefully. The `processed_order_ids` collectionorderId`s of the corresponding TP and SL orders as soon as they become active on the exchange.

-   **Atom in Firestore acts as a lock to ensure that the same closed trade is never logged to BigQuery more than once, even if the `/icity (Log First, Then Delete)**: A trade record is deleted from the operational `active_orders` collection **log-pnl` process is run multiple times over the same period.

## Detailed Data Flow & Logic

This section describes the complete lifecycle of a trade within the system.

### Phase 1: Order Placement (`/process-alerts` cycle)

*   **Trigger:** Cloud Scheduler calls the `/process-alerts` endpoint every minute.
*   **Action:**
only after** it has been successfully and permanently logged in the `real_trades_history` table in BigQuery. This prevents data loss in case of transient errors.

-   **Idempotency**: The `processed_order_ids` collection in Firestore acts as a lock, ensuring that the same closed trade from Bybit is never processed and logged to BigQuery more than once, even if fetched multiple times.

## Services Deep Dive

### 1. `firestore-webhook-receiver`
    1.  The service queries the `alerts` collection for any new alerts received since the last check.
    2.  Each new alert is validated (e.g., logical SL/Entry prices, no existing position for the symbol).
    3.  If-   **Type**: Google Cloud Function
-   **Trigger**: HTTP POST request from TradingView webhooks.
-   **Role**: an alert is valid, the system cancels any previous, unfilled `LIMIT` orders for that same symbol on Bybit to avoid
    1.  Receives incoming JSON alert payloads from TradingView.
    2.  Validates a shared duplicate positions.
    4.  A unique `orderLinkId` is generated.
    5.  A single, secret (`secret_token`) to ensure authenticity.
    3.  Saves the validated alert data with a `received_at` timestamp into the `alerts` collection in Firestore.

### 2. `data-collector-service` ( integrated `LIMIT` order is sent to the Bybit API, containing the entry price, Stop Loss, Take Profit, and our custom `orderLinkId`.
    6.  Upon successful placement, Bybit returns its own `limitOrderId`.
    7.  AOptional)
-   **Type**: Cloud Run Container
-   **Trigger**: Cloud Scheduler job (`invoke-data-collector`), typically every minute.
-   **Role**:
    1.  Fetches the list of symbols "golden record" is created in the `active_orders` collection in Firestore. The document ID is our `orderLinkId`. to watch from Firestore (`bot_config` collection).
    2.  Asynchronously queries the Bybit API for This record contains all planned data for the trade and is given an initial `status` of `'PLACED'`.

### the latest market klines (candles) for all symbols.
    3.  Saves the kline data into the `latest_klines` collection in Firestore, which acts as a cache.
-   **Note**: This service was Phase 2: Order Monitoring & Enrichment (`/update-orders` cycle)

*   **Trigger:** Cloud Scheduler calls the `/update-orders` endpoint every 2 minutes. This process is crucial for solving the race condition where a trade might open and close before it originally intended for an analytical mode. If this mode is not in use, this service and its corresponding Cloud Scheduler job can be **disabled to can be properly tracked.
*   **Action:**
    1.  The service queries the `active_orders` collection save costs**.

### 3. `trading-bot-service`
This is the core engine of the system for all documents with `status: 'PLACED'`.
    2.  For each order, it queries the Bybit API using the `orderLinkId` to check its current status.
    3.  **If the order status is `Filled`:**
, containing all the trading and reconciliation logic. It exposes three critical endpoints triggered by Cloud Scheduler.

-   **`/process-alerts`** (Triggered every minute)
    -   Responsible for **opening new trades**.
    -   Scans the `alerts        a. The position is now open. The service immediately queries Bybit again to get the list of active conditional orders (` collection for new entries since its last run.
    -   For each valid new alert, it cancels any existingTP/SL) for that symbol.
        b. It finds the specific TP and SL orders by matching their ` limit orders for that symbol, calculates the position size, and places a single, unified LIMIT order on Bybit containing the entry price, Stop Loss, and Take Profit.
    -   Crucially, it creates the "golden record" in the `active_orders` collection with `status: 'PLACED'`.

-   **`/update-orders`** (Triggered every 2 minutestriggerPrice` with the planned prices stored in our Firestore document.
        c. It extracts the unique `orderId` for)
    -   Responsible for **monitoring and enriching active orders**.
    -   Scans the `active_orders` collection for documents with `status: 'PLACED'`.
    -   For each, it checks the order's status on Bybit using its the Take Profit and Stop Loss orders.
        d. The Firestore document is updated with these new IDs (`tpOrderId`, `slOrderId`) and its `status` is changed to `'OPEN'`.
    4.  **If the order status is `New` or `PartiallyFilled`:** The service does nothing and will check again in the next cycle.
    5.  **If the order `orderLinkId`.
    -   If the order status is `Filled`, it means the position is now open. The status is `Cancelled` or `Rejected`:** The document's `status` is updated to `'CANCELLED'`.

### Phase 3 service then fetches the active TP and SL orders for that symbol, finds their unique `orderId`s, and updates: P&L Logging (`/log-pnl` cycle)

*   **Trigger:** Cloud Scheduler calls the `/log-pnl` endpoint every 15 minutes.
*   **Action:**
    1.  The service fetches the history of recently closed positions the Firestore document with these IDs and a new `status: 'OPEN'`.

-   **`/log-p (the P&L history) from Bybit.
    2.  For each closed trade, it begins the **nl`** (Triggered every 15 minutes)
    -   Responsible for **reconciling and logging closed trades**.
    -   Fetches the history of recently closed positions (PnL records) from Bybit.
    -matching process**:
        a. **Primary Method:** It takes the `orderId` of the closing trade (e.g.,   For each closed trade, it attempts to find a match in `active_orders` using a robust, multi-step the ID of the SL that was hit) and queries the `active_orders` collection to find a document where either process.
    -   Once a match is found, it combines the planned data from Firestore with the realized data from Bybit to the `tpOrderId` or `slOrderId` field matches. This is the fastest and most reliable matching path.
        b. **Advanced create a complete trade record.
    -   It writes this record to the `real_trades_history` table in BigQuery.
    -   Finally, it deletes the corresponding record from `active_orders`.

## Core Workflows

### Trade Opening Flow (`/process-alerts`) Fallback:** If the primary method fails (e.g., in case of a liquidation or a rare race condition), the system queries the Bybit order history for the closing trade's `orderId` to retrieve its associated `orderLinkId`. It then uses this `orderLinkId` to find the "golden record" in `active_orders`.
    3.  **Grace
1.  Cloud Scheduler triggers `/process-alerts`.
2.  The service fetches new alerts from the `alerts` collection. Period:** If no match is found by any method and the trade was closed very recently (e.g., < 3 minutes ago
3.  For the latest valid alert for a given symbol, it performs validations (e.g., no open), it is ignored in the current cycle to be re-processed later. This provides an extra layer of protection against race conditions.
    4. position).
4.  It cancels any pre-existing, unfilled LIMIT orders for that symbol on Bybit.
5.  It  **Final Record Creation:**
        *   If a match is found, the system merges the planned data from Firestore generates a unique `orderLinkId`.
6.  It places a single LIMIT order on Bybit with Entry, SL with the actual execution data from Bybit to create a complete trade record.
        *   If no match is found after, and TP prices, including the `orderLinkId`.
7.  Upon confirmation from Bybit, it creates a all checks, the trade is logged with an `alert_id` of `UNMATCHED_OR_MANUAL`.
    5.  ** new document in `active_orders` using the `orderLinkId` as the document ID. This record is saved with `status: 'PLACED'`.

### Order Monitoring Flow (`/update-orders`)
1.  Cloud Scheduler triggers `/update-orders`.
2.  Atomic Write & Cleanup:** The complete record is **always** written to the `real_trades_history` table in BigQuery.The service queries `active_orders` for all documents where `status` is `PLACED`.
3.  For **Only if** the write to BigQuery is successful **and** the trade was matched, the corresponding document is deleted each document, it queries the Bybit API using the `orderLinkId` to check the status of the opening LIMIT order.
4.  If the status is `Filled`:
    a. The position is now open.
    b. The service queries Bybit again from the `active_orders` collection.

## Technology Stack

*   **Language:** Python 3.11
*   **Framework:** Flask & Gunicorn
*   **Compute:** Google Cloud Run, Google Cloud Functions
*   **Or to get all active conditional orders (TP/SL) for the symbol.
    c. It matches the TP andchestration:** Google Cloud Scheduler
*   **Database:** Google Firestore (for operational data)
*   **Data Warehouse:** Google BigQuery (for analytics)
*   **Deployment:** Google Cloud Build with Docker
*   **Secrets Management:** SL orders by comparing their `triggerPrice` with the `planned_tp_price` and `planned_sl_ Google Secret Manager

## Setup & Configuration

### Environment Variables

The following environment variables should be set for the services:

*   `GCP_PROJECT`: Your Google Cloud Project ID.
*   `USE_TESTNET`: Setprice` from the Firestore document.
    d. It updates the Firestore document, adding the `tpOrderId` and `slOrderId` and changing the `status` to `OPEN`.

### Trade Reconciliation Flow (`/log-pnl`)
1.  Cloud Scheduler triggers to `"true"` to use the Bybit testnet, or `"false"` for the mainnet.

### Secrets

The application requires the following secrets to be configured in **Google Secret Manager**:

*   `bybit-api-key`: Your `/log-pnl`.
2.  The service fetches the recent closed PnL history from Bybit.
3.  For each closed trade, it gets the closing `orderId` (e.g., the ID of the SL or TP that was hit Bybit API key.
*   `bybit-api-secret`: Your Bybit API secret.
*   `WEBHOOK).
4.  **Primary Matching Strategy**: It queries `active_orders` to find a document where either `_SECRET_TOKEN`: A secret token used to authenticate webhooks from TradingView.

These secrets are accessed by the CloudtpOrderId` or `slOrderId` matches the closing `orderId`.
5.  **Advanced Fallback Strategy** Run services via their service accounts.

## Deployment

Deployment is automated via **Cloud Build**. The repository contains `cloudbuild-bot.yaml` and (for liquidations or race conditions): If the primary match fails, it queries Bybit's order history using the closing `orderId` to retrieve `cloudbuild-collector.yaml` files that define the build and deploy steps. These are typically connected to a Git repository, triggering a new its `orderLinkId`, and then uses that `orderLinkId` to find the correct document in `active_orders`.
6.  It combines data from the PnL record and the matched `active_orders` document.
7.  It writes the deployment on every push to the main branch.

The build process involves:
1.  Building the Docker image for the service.
2.   final, enriched record to BigQuery.
8.  If the write to BigQuery is successful and the trade was matched, it deletes the documentPushing the image to Google Artifact Registry.
3.  Deploying the new image to the corresponding Cloud Run service, from `active_orders`.

## Technology Stack

-   **Language**: Python 3.11
-   **Framework**: ensuring zero-downtime updates.