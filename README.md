# Automated Trading Bot for Bybit on Google Cloud

## Overview

This project is an automated, event-driven trading bot designed to execute trades on the Bybit exchange. It operates based on webhook alerts triggered from TradingView and is built entirely on a serverless architecture using Google Cloud Platform (GCP).

The system is designed for resilience, scalability, and cost-efficiency, leveraging microservices on Cloud Run, a NoSQL database for operational data (Firestore), and a data warehouse for historical analysis (BigQuery).

## System Architecture

The application consists of several decoupled services and components that work together to manage the lifecycle of a trade, from alert reception to final P&L logging.

**Key Components:**

1.  **TradingView:** The source of trading signals. It sends alerts via webhooks when specific market conditions are met.
2.  **`firestore-webhook-receiver` (Cloud Function):** A secure endpoint that ingests alerts from TradingView, validates them, and stores them as new documents in the `alerts` collection in Firestore.
3.  **`trading-bot-service` (Cloud Run):** The core of the system, containing the main trading logic. It runs three independent, scheduled processes:
    *   **/process-alerts:** Opens new positions.
    *   **/update-orders:** Monitors placed orders to enrich them with live data.
    *   **/log-pnl:** Matches and logs closed trades.
4.  **`data-collector-service` (Cloud Run):** A supplementary service that periodically fetches market data (klines/candles) from Bybit and caches it in Firestore. This is primarily used for back-testing or analytical modes.
5.  **Cloud Scheduler:** The orchestration engine that triggers the different processes (`/process-alerts`, `/update-orders`, `/log-pnl`) on a defined schedule.
6.  **Firestore:** The operational NoSQL database used for storing alerts, active order data, and system state.
7.  **BigQuery:** The analytical data warehouse where final, immutable trade history records are stored for performance analysis.
8.  **Secret Manager:** Securely stores sensitive information like API keys.

## Core Concepts

The system is built on a few fundamental principles:

*   **`orderLinkId` as the Golden Thread:** We generate a unique, custom `orderLinkId` for every trade operation. This ID is the single source of truth that links the initial alert, the opening order on the exchange, and the final closing trade, even though the exchange assigns different `orderId`s for opening and closing orders.
*   **Separation of Concerns:** The trading logic is split into three distinct, asynchronous processes, each with a single responsibility. This makes the system more resilient and easier to debug.
    1.  **Placing Orders:** Handled exclusively by `/process-alerts`.
    2.  **Monitoring Open Orders:** Handled by `/update-orders`.
    3.  **Logging Closed Trades:** Handled by `/log-pnl`.
*   **Proactive Data Enrichment:** Instead of trying to find trade details after the fact, the system uses a dedicated process (`/update-orders`) to monitor newly filled orders and proactively enrich the `active_orders` record with the `orderId`s of the corresponding TP and SL orders as soon as they become active on the exchange.
*   **Atomicity (Log First, Then Delete):** A trade record is deleted from the operational `active_orders` collection **only after** it has been successfully and permanently logged in the `real_trades_history` table in BigQuery. This prevents data loss in case of transient errors.
*   **Idempotency:** The `processed_order_ids` collection in Firestore acts as a lock, ensuring that the same closed trade from Bybit is never processed and logged to BigQuery more than once, even if fetched multiple times.

## Detailed Data Flow & Logic

This section describes the complete lifecycle of a trade within the system.

### Phase 1: Order Placement (`/process-alerts` cycle)

*   **Trigger:** Cloud Scheduler calls the `/process-alerts` endpoint every minute.
*   **Action:**
    1.  The service queries the `alerts` collection for any new alerts received since the last check.
    2.  Each new alert is validated (e.g., logical SL/Entry prices, no existing position for the symbol).
    3.  If an alert is valid, the system cancels any previous, unfilled `LIMIT` orders for that same symbol on Bybit to avoid duplicate positions.
    4.  A unique `orderLinkId` is generated.
    5.  A single, integrated `LIMIT` order is sent to the Bybit API, containing the entry price, Stop Loss, Take Profit, and our custom `orderLinkId`.
    6.  Upon successful placement, Bybit returns its own `limitOrderId`.
    7.  A "golden record" is created in the `active_orders` collection in Firestore. The document ID is our `orderLinkId`. This record contains all planned data for the trade and is given an initial `status` of `'PLACED'`.

### Phase 2: Order Monitoring & Enrichment (`/update-orders` cycle)

*   **Trigger:** Cloud Scheduler calls the `/update-orders` endpoint every 2 minutes. This process is crucial for solving the race condition where a trade might open and close before it can be properly tracked.
*   **Action:**
    1.  The service queries the `active_orders` collection for all documents with `status: 'PLACED'`.
    2.  For each order, it queries the Bybit API using the `orderLinkId` to check its current status.
    3.  **If the order status is `Filled`:**
        a. The position is now open. The service immediately queries Bybit again to get the list of active conditional orders (TP/SL) for that symbol.
        b. It finds the specific TP and SL orders by matching their `triggerPrice` with the planned prices stored in our Firestore document.
        c. It extracts the unique `orderId` for the Take Profit and Stop Loss orders.
        d. The Firestore document is updated with these new IDs (`tpOrderId`, `slOrderId`) and its `status` is changed to `'OPEN'`.
    4.  **If the order status is `New` or `PartiallyFilled`:** The service does nothing and will check again in the next cycle.
    5.  **If the order status is `Cancelled` or `Rejected`:** The document's `status` is updated to `'CANCELLED'`.

### Phase 3: P&L Logging (`/log-pnl` cycle)

*   **Trigger:** Cloud Scheduler calls the `/log-pnl` endpoint every 15 minutes.
*   **Action:**
    1.  The service fetches the history of recently closed positions (the P&L history) from Bybit.
    2.  For each closed trade, it begins the **matching process**:
        a. **Primary Method:** It takes the `orderId` of the closing trade (e.g., the ID of the SL that was hit) and queries the `active_orders` collection to find a document where either the `tpOrderId` or `slOrderId` field matches. This is the fastest and most reliable matching path.
        b. **Advanced Fallback:** If the primary method fails (e.g., in case of a liquidation or a rare race condition), the system queries the Bybit order history for the closing trade's `orderId` to retrieve its associated `orderLinkId`. It then uses this `orderLinkId` to find the "golden record" in `active_orders`.
    3.  **Grace Period:** If no match is found by any method and the trade was closed very recently (e.g., < 3 minutes ago), it is ignored in the current cycle to be re-processed later. This provides an extra layer of protection against race conditions.
    4.  **Final Record Creation:**
        *   If a match is found, the system merges the planned data from Firestore with the actual execution data from Bybit to create a complete trade record.
        *   If no match is found after all checks, the trade is logged with an `alert_id` of `UNMATCHED_OR_MANUAL`.
    5.  **Atomic Write & Cleanup:** The complete record is **always** written to the `real_trades_history` table in BigQuery. **Only if** the write to BigQuery is successful **and** the trade was matched, the corresponding document is deleted from the `active_orders` collection.

## Technology Stack

*   **Language:** Python 3.11
*   **Framework:** Flask & Gunicorn
*   **Compute:** Google Cloud Run, Google Cloud Functions
*   **Orchestration:** Google Cloud Scheduler
*   **Database:** Google Firestore (for operational data)
*   **Data Warehouse:** Google BigQuery (for analytics)
*   **Deployment:** Google Cloud Build with Docker
*   **Secrets Management:** Google Secret Manager

## Setup & Configuration

### Environment Variables

The following environment variables should be set for the services:

*   `GCP_PROJECT`: Your Google Cloud Project ID.
*   `USE_TESTNET`: Set to `"true"` to use the Bybit testnet, or `"false"` for the mainnet.

### Secrets

The application requires the following secrets to be configured in **Google Secret Manager**:

*   `bybit-api-key`: Your Bybit API key.
*   `bybit-api-secret`: Your Bybit API secret.
*   `WEBHOOK_SECRET_TOKEN`: A secret token used to authenticate webhooks from TradingView.

These secrets are accessed by the Cloud Run services via their service accounts.

## Deployment

Deployment is automated via **Cloud Build**. The repository contains `cloudbuild-bot.yaml` and `cloudbuild-collector.yaml` files that define the build and deploy steps. These are typically connected to a Git repository, triggering a new deployment on every push to the main branch.

The build process involves:
1.  Building the Docker image for the service.
2.  Pushing the image to Google Artifact Registry.
3.  Deploying the new image to the corresponding Cloud Run service, ensuring zero-downtime updates.
