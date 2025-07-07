Oczywiście. Z przyjemnością przygotuję zaktualizowaną i profesjonalną dokumentację README.md w języku angielskim, która odzwierciedla wszystkie wprowadzone zmiany, obecną architekturę i logikę działania systemu. Poniżej znajduje się gotowy plik.

Automated Trading Bot v8.0 (Google Cloud Platform)
1. Project Description and Main Goal

The Automated Trading Bot v8.0 is a fully automated, serverless trading system operating 24/7 on the Google Cloud Platform (GCP). Its primary objective is to autonomously execute and perform advanced analysis of a trading strategy based on the "Smart Money" concept, specifically focusing on Order Block (OB) formations.

The system is designed for maximum reliability, security, and performance by decoupling the data collection process from the core trading logic. It aims to minimize operational costs and ensure analytical precision through a robust, multi-component serverless architecture, where all logic is deployed as containerized microservices on Cloud Run.

2. Operational Cycle and Core Business Logic

The system operates based on a precisely defined, multi-stage data and decision flow:

Stage 1: Signal Generation (TradingView)

Source: A custom indicator written in Pine Script on the TradingView platform.

Indicator Logic: The indicator analyzes the price chart in real-time, searching for "New OB" (New Order Block) formations.

Action: Upon identifying a new, valid OB, the indicator triggers an alert via a webhook.

Stage 2: Alert Ingestion and Queuing (GCP)

Service: A lightweight Cloud Function (gcp-webhook) acting as the webhook endpoint.

Flow:

The webhook from TradingView sends a POST request with the alert data (JSON) to the function's public URL.

The function immediately saves the raw alert as a new document in the alerts collection in Cloud Firestore, which serves as a durable input queue.

Stage 3: High-Performance Data Collection (Independent Microservice on Cloud Run)
To ensure reliability and mitigate API latency, a dedicated data collection service runs independently and asynchronously.

Service: A containerized Python application (data-collector-service) on Cloud Run, triggered every minute by Cloud Scheduler.

Flow:

The service reads a dynamic list of symbols to monitor from a configuration document in Firestore (bot_config/symbols_config). This allows for live updates without redeploying code.

It concurrently and asynchronously queries the Bybit API for the latest 1-minute candles (klines) for all symbols, drastically reducing data collection time.

It saves the high, low, and close prices for each symbol to a dedicated collection in Firestore: latest_klines. This collection acts as a fast and reliable internal data cache.

Stage 4: Cyclical Processing and Execution (Main Bot on Cloud Run)

Orchestration: A separate Cloud Scheduler job invokes the main bot service (trading-bot-service) every minute.

Main Bot Flow:

4.1. Setup Management: The bot reads new alerts from the alerts queue and validates them using Pydantic models. Each new alert for a given symbol overwrites the previous "active setup" in the active_setups collection, invalidating the Old OB.

4.2. Opening New Positions:

Data Source: The bot reads the latest price data for all monitored symbols directly from the latest_klines cache in Firestore. It no longer queries the Bybit API, making the process faster and more resilient to API failures.

Decision Logic: It iterates through all active setups and, based on the cached high and low prices of the last candle, decides whether to open a new position.

State Management: It correctly identifies Fresh OB (first entry attempt) vs. Used OB (subsequent entry attempts) and handles the price reset logic after a LOSE. Upon entry, it creates an isolated document in the open_trades collection, "freezing" all transaction parameters (SL, TP, etc.).

4.3. Monitoring Open Positions: The bot iterates through all documents in the open_trades collection. It uses fresh data from the Firestore cache to check if the "frozen" SL or TP levels of any position have been breached.

4.4. Finalization and Optimized Post-Mortem Analysis:

Initial BigQuery Write: When a position is closed (WIN/LOSE), the bot fetches the historical kline data for the trade's duration, calculates the maximum achieved R:R, and writes a complete, initial record to BigQuery.

Creating a "Ghost": A "ghost" of the closed trade is created in the analyzed_trades collection in Firestore, storing its initial state.

Efficient Passive Tracking: In subsequent cycles, the bot optimally tracks this "ghost." Instead of fetching the entire trade history, it only fetches new candles since the last check. If the price reaches a new, higher TP level, it sends a secure, parameterized UPDATE query to BigQuery to enrich the existing record. This process continues until tp_5_0 or the original sl is reached, at which point the "ghost" is deleted.

3. Technical Architecture

The system utilizes a decoupled, secure, and high-performance microservices architecture on the Google Cloud Platform.

Data Ingestion: TradingView (Webhook) -> Cloud Function -> Cloud Firestore (alerts collection).

Data Collection (Caching): Cloud Scheduler -> Cloud Run (data-collector-service) -> Bybit API -> Cloud Firestore (latest_klines collection).

Orchestration: Cloud Scheduler (1-minute cron triggers for both services).

Core Application: Cloud Run (Docker containers for trading-bot-service and data-collector-service), built using the Application Factory pattern for robustness.

Security:

GCP Secret Manager: All sensitive data (like API keys) is stored securely and accessed via IAM roles, not in code or .env files in the production environment.

Parameterized Queries: All UPDATE operations on BigQuery are parameterized to prevent SQL Injection vulnerabilities.

State Management (Firestore):

bot_config: Stores dynamic application configuration, such as the list of symbols to watch.

alerts: A durable queue for incoming signals.

latest_klines: A real-time price cache, updated by the collector.

active_setups, open_trades, analyzed_trades: Collections managing the state of the trading logic.

Analytics and Logging:

Cloud Logging: A central hub for real-time monitoring, with structured log names for easy filtering.

BigQuery: An analytical data warehouse for all closed trades.

Network Infrastructure:

Serverless VPC Access Connector & Cloud NAT: Provide a static egress IP address for all outgoing traffic.

Deployment Automation (CI/CD):

GitHub Actions & Cloud Build: Workflows in GitHub Actions trigger builds in Google Cloud Build. Each service has a dedicated cloudbuild-*.yaml configuration file to ensure correct and isolated builds using the appropriate Dockerfile.

4. Key Design Principles

Decoupling: Data collection is fully separated from the trading logic. The main bot is resilient to API failures as it relies on an internal cache.

Security First: Secrets are managed by GCP Secret Manager, and database queries are secured against injection attacks.

High Performance: Asynchronous, parallel data fetching in the collector minimizes latency.

Robustness and Fault Tolerance: The application is written defensively, using Pydantic models for data validation and the Application Factory pattern to prevent issues with Gunicorn process management.

Flexibility: The list of traded symbols is managed dynamically in Firestore without requiring code changes.

5. Project Structure

The project is organized into dedicated, isolated directories for each microservice, a shared library, and the webhook function.

.
├── .github/
│   └── workflows/
│       ├── deploy-collector.yml    # GitHub Actions workflow for the data collector
│       └── deploy.yml              # GitHub Actions workflow for the main trading bot
├── bot_service/                    # Code for the main trading bot
│   ├── __init__.py
│   ├── bigquery_logger.py
│   ├── bot_logic.py
│   ├── Dockerfile
│   ├── fetch_from_firestore.py
│   ├── main.py
│   ├── positions_logger.py
│   └── state_manager.py
├── collector_service/              # Code for the data collector microservice
│   ├── __init__.py
│   ├── collector_main.py
│   ├── data_collector.py
│   └── Dockerfile
├── gcp-webhook/                    # Code for the isolated Cloud Function (webhook receiver)
│   ├── main.py
│   └── requirements.txt
├── shared_lib/                     # Shared code used by both microservices
│   ├── __init__.py
│   ├── config_loader.py
│   ├── constants.py
│   ├── firebase_client.py
│   └── models.py
├── .env.example                    # Example file for local environment variables
├── .gitignore
├── .gcloudignore                   # Specifies files to ignore when deploying to GCP
├── cloudbuild-bot.yaml             # Cloud Build configuration for the bot service
├── cloudbuild-collector.yaml       # Cloud Build configuration for the collector service
├── README.md
└── requirements.txt                # Central requirements file for both services

Key File Descriptions:

bot_service/main.py & collector_service/collector_main.py: Entry points (Flask) for both services, using the Application Factory pattern.

bot_service/bot_logic.py: The "brain" of the system, containing the core business logic.

collector_service/data_collector.py: The core logic for the data collector, performing asynchronous data fetching.

shared_lib/: A directory containing modules shared by both microservices:

config_loader.py: A module for securely loading configuration from GCP Secret Manager or a local .env file.

models.py: Defines Pydantic data models, ensuring data integrity and improving code readability.

firebase_client.py, bigquery_logger.py: Modules abstracting communication with GCP services.

constants.py: A central place for constants and configuration parameters.

gcp-webhook/main.py: Isolated code for the Cloud Function that ingests alerts.

.../Dockerfile: Definitions for building the container images for each service.

cloudbuild-*.yaml: Dedicated configuration files for Google Cloud Build, ensuring the correct build process for each service.

.github/workflows/*.yml: CI/CD pipeline definitions that trigger the appropriate Cloud Build configurations based on changed files.