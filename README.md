Automated Trading Bot v6.8 (GCP)
1. Project Description and Main Goal

The Automated Trading Bot v6.8 is a fully automated, serverless trading system running 24/7 on the Google Cloud Platform (GCP). Its primary objective is the autonomous execution and advanced analysis of a trading strategy based on the "Smart Money" concept, specifically targeting Order Block (OB) formations.

The system is designed for maximum reliability and fault tolerance by decoupling data collection from the core trading logic. It aims to minimize operational costs and ensure analytical precision through a robust, multi-component serverless architecture, where all custom logic is deployed as containerized services on Cloud Run.

2. Operational Cycle and Core Business Logic

The system operates based on a precisely defined, multi-stage data and decision flow:

Stage 1: Signal Generation (TradingView)

Source: A custom Pine Script indicator on the TradingView platform.

Indicator Logic: The indicator analyzes the price chart in real-time for New OB (Order Block) formations.

Action: Upon identifying a new, valid New OB, the indicator triggers an alert via a webhook.

Stage 2: Alert Ingestion and Queuing (GCP)

Service: A lightweight Cloud Function (gcp-webhook) acting as a webhook endpoint.

Flow:

The webhook from TradingView sends a POST request with the alert data (JSON) to the function's public URL.

The function immediately saves the raw alert as a new document in the alerts collection in Cloud Firestore, which serves as a persistent input queue.

Stage 3: Data Collection (Independent Microservice on Cloud Run)

To ensure reliability and mitigate API latency issues, a dedicated data collection service runs independently.

Service: A containerized Python application (data-collector-service) on Cloud Run, triggered every minute by Cloud Scheduler.

Flow:

The service fetches a predefined list of symbols to monitor (SYMBOLS_TO_WATCH).

It queries the Bybit API for the latest 1-minute kline data for each symbol, using a built-in retry mechanism to handle transient network errors.

It saves the high, low, and close prices for each symbol into a dedicated Firestore collection: latest_klines. This collection acts as a fast, reliable, internal data cache.

Stage 4: Cyclical Processing and Execution (Main Bot on Cloud Run)

Orchestration: A separate Cloud Scheduler job invokes the main bot service every minute.

Main Bot: A Cloud Run service (trading-bot-service) that performs the following steps:

4.1. Setup Management

The bot reads new alerts from the alerts queue.

Each new alert for a given symbol overwrites the previous "active setup" in the active_setups collection. This invalidates the Old OB as a basis for opening new positions.

4.2. Opening New Positions

The bot directly queries the Bybit API for the latest price data to ensure maximum accuracy at the moment of decision.

It iterates through all active setups and, based on the high and low of the latest candle, decides whether to open a new position.

It correctly identifies Fresh OB (first entry) vs. Used OB (subsequent entries) and handles the price reset logic after a LOSE.

Upon entry, it creates an isolated document in the open_trades collection, "freezing" all transaction parameters (SL, TP, etc.).

4.3. Monitoring Open Positions

The bot iterates through all documents in the open_trades collection, which is completely independent of the setups.

It uses fresh kline data from the Bybit API to check if any position's "frozen" sl or tp has been breached.

4.4. Finalization and Post-Mortem Analysis

Initial Log to BigQuery: When a position is closed (WIN/LOSE), a one-time historical analysis is performed. The bot fetches all kline data for the trade's duration, calculates the maximum achieved R:R, and writes a complete, initial record to BigQuery.

"Ghost" Creation: A "ghost" of the closed trade is created as a document in the analyzed_trades collection in Firestore.

Passive Tracking: In subsequent cycles, the bot passively tracks this ghost against live prices. If the price reaches a new, higher TP level, the bot sends an UPDATE query to BigQuery to enrich the existing record. This continues until tp_5_0 or the original sl is hit, at which point the ghost is deleted.

3. Technical Architecture

The system employs a decoupled, microservice-oriented architecture on GCP.

Data Ingestion: TradingView (Webhook) -> Cloud Function -> Cloud Firestore (collection 'alerts')

Data Collection: Cloud Scheduler -> Cloud Run (data-collector-service) -> Bybit API -> Cloud Firestore (collection 'latest_klines')

Orchestration: Cloud Scheduler (1-minute cron triggers for both the data collector and the main bot).

Application Core: Cloud Run (Docker containers for trading-bot-service and data-collector-service).

State Management (Firestore):

alerts: Persistent queue for incoming signals.

latest_klines: Real-time price cache, updated by the collector.

active_setups: Stores the latest, active setup (New OB) for each symbol.

open_trades: Stores the state of each individual, open position.

analyzed_trades: Stores data of closed positions for passive post-mortem analysis.

Analytics & Logging:

Cloud Logging: Central hub for real-time monitoring and debugging.

BigQuery: Analytical data warehouse serving as a permanent archive for all closed trades, with records being updated post-mortem.

Network Infrastructure:

Serverless VPC Access Connector & Cloud NAT: Provide a static, European IP address for all outbound traffic.

Deployment Automation (CI/CD):

GitHub Actions: Separate workflows for each service, triggered by changes to relevant files on the div branch.

4. Key Design Principles

Decoupling: Data collection is separated from trading logic.

State Isolation: An open position is an independent entity. The arrival of a new setup does not affect its monitoring.

Data Precision: All trading decisions are based on the analysis of a candle's high and low.

Fault Tolerance: The application is written defensively to handle incomplete data, API errors, and race conditions.

Advanced Strategy Logic: The system correctly implements the Fresh OB vs. Used OB logic, including the price reset condition after a LOSE, and features a two-stage analytics pipeline (initial log + post-mortem updates).

5. Project Structure

The project is organized into a main application directory and a separate directory for the webhook function.

Generated code
.
├── .github/
│   └── workflows/
│       ├── deploy-collector.yml  # CI/CD for Data Collector
│       └── deploy.yml            # CI/CD for Trading Bot
├── gcp-webhook/
│   ├── main.py
│   └── requirements.txt
├── .env
├── .gitignore
├── Dockerfile                  # For Data Collector
├── Dockerfile.bot              # For Trading Bot
├── README.md
├── requirements.txt
├── bot_logic.py
├── bigquery_logger.py
├── collector_main.py
├── constants.py
├── data_collector.py
├── fetch_from_firestore.py
├── firebase_client.py
└── main.py
└── state_manager.py

Key File Descriptions:

main.py: The main entry point (Flask) for the trading bot service. Orchestrates the cycle by fetching alerts and invoking bot_logic.

bot_logic.py: The brain of the system. Contains the core business logic for opening, managing, and analyzing trades.

collector_main.py: The Flask entry point for the data collector service.

data_collector.py: The core logic for the data collector, responsible for fetching and caching kline data.

state_manager.py: A dedicated module for abstracting all state management operations on Firestore collections.

bigquery_logger.py: The module responsible for all communication with BigQuery, including INSERT and UPDATE operations.

constants.py: A central place for all constants and configuration parameters.

gcp-webhook/main.py: The isolated code for the alert ingestion Cloud Function.

Dockerfile / Dockerfile.bot: Definitions for building the container images for the two Cloud Run services.

.github/workflows/*.yml: CI/CD pipeline definitions for automated deployment.