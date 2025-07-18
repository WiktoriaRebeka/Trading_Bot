Automated Trading Bot on GCP
<!-- Add badges here (e.g., build status, license) -->
<!-- [![CI/CD Status](https://github.com/your-repo/actions/workflows/deploy.yml/badge.svg)](https://github.com/your-repo/actions/workflows/deploy.yml) -->
A fully automated, serverless trading system built on Google Cloud Platform. This project implements a "Smart Money" trading strategy based on Order Block (OB) formations, designed for high reliability, security, and performance.
The architecture is fully decoupled, separating data collection from the core trading logic. This ensures the bot is resilient to external API failures and can operate 24/7 in a cost-effective manner using containerized microservices on Cloud Run.
Core Features
Automated Strategy Execution: Autonomously executes a trading strategy based on Order Block (OB) formations identified by a custom TradingView indicator.
Decoupled & Resilient Architecture: The core trading logic is isolated from live market data fetching, operating on a reliable internal data cache. This makes the system resilient to external API latency or outages.
Efficient Post-Mortem Analysis: Utilizes a "ghost" tracking mechanism to analyze the maximum potential of winning trades with minimal computational overhead, enriching historical data directly in BigQuery.
Secure by Design: Leverages GCP Secret Manager for all credentials (no hardcoded keys), IAM for fine-grained permissions, and parameterized queries to prevent SQL injection.
Dynamic Configuration: The list of monitored trading symbols can be updated live via a Firestore document, without requiring a code deployment.
Automated CI/CD: Fully automated deployment pipeline using GitHub Actions and Google Cloud Build for safe and consistent releases.
System Architecture and Data Flow
The system is built on a decoupled, event-driven microservices architecture hosted entirely on GCP.
Data Flow Diagram:
[TradingView] --(Webhook)--> [Cloud Function] --(Write)--> [Firestore: alerts]
                                                                  ^
                                                                  |
[Cloud Scheduler] --(Trigger)--> [Data Collector Service] --(Cache)--> [Firestore: latest_klines]
            |                            |
            |                            +----(Fetch)-----> [Bybit API]
            |
            +--(Trigger)--> [Trading Bot Service] --(Read)--> [Firestore: alerts, latest_klines, ...]
                                     |
                                     +----(Write/Update)----> [BigQuery: trade_history]

Step-by-step Process:
Signal Ingestion: A custom Pine Script indicator on TradingView detects an Order Block and sends an alert via webhook to a secured Cloud Function. The function validates the request and saves the raw alert to the alerts collection in Cloud Firestore, which acts as a durable input queue.
Market Data Caching: Every minute, Cloud Scheduler triggers the data-collector-service (a Cloud Run microservice). This service asynchronously fetches the latest 1-minute klines from the Bybit API for all configured symbols and saves them to the latest_klines collection in Firestore. This collection serves as a fast, reliable internal cache of market data.
Core Logic Execution: Independently, Cloud Scheduler triggers the main trading-bot-service every minute. This service:
Reads new signals from the alerts queue and market data from the latest_klines cache.
Manages the state of trading setups (active_setups).
Opens new positions by creating documents in the open_trades collection.
Monitors and closes existing positions based on SL/TP levels.
Writes the initial results of closed trades to BigQuery.
Post-Mortem Analysis ("Ghost" Tracking): For winning trades, a "ghost" document is created in the analyzed_trades collection. In subsequent cycles, the bot efficiently checks for new price highs against the ghost's parameters. If a higher profit target is reached, it enriches the existing trade record in BigQuery with a lightweight UPDATE query. The ghost is deleted once the analysis is complete.
Technology Stack
Cloud Provider: Google Cloud Platform (GCP)
Compute: Cloud Run, Cloud Functions
Database: Cloud Firestore (NoSQL), BigQuery (Data Warehouse)
Orchestration: Cloud Scheduler
Security: Secret Manager, IAM, VPC Access Connector, Cloud NAT
CI/CD: GitHub Actions, Google Cloud Build
Language: Python 3.11+
Framework: Flask (using Application Factory pattern)
Key Libraries: pydantic, google-cloud-firestore, google-cloud-bigquery, aiohttp
Project Structure
The project is a monorepo with dedicated directories for each microservice and a shared library to promote code reuse and maintainability.

Project Structure
The project is a monorepo with dedicated directories for each microservice and a shared library to promote code reuse and maintainability.

TRADING_BOT/
├── bot_service/
│   ├── __init__.py
│   ├── app_setup.py
│   ├── bigquery_logger.py
│   ├── bot_logic.py
│   ├── Dockerfile
│   ├── fetch_from_firestore.py
│   ├── main.py
│   ├── requirements.txt
│   └── state_manager.py
├── collector_service/
│   ├── __init__.py
│   ├── app_setup.py
│   ├── collector_main.py
│   ├── data_collector.py
│   ├── Dockerfile
│   └── requirements.txt
├── gcp-webhook/
├── shared_lib/
│   ├── __init__.py
│   ├── config_loader.py
│   ├── config.py
│   ├── constants.py
│   ├── firebase_client.py
│   └── models.py
├── test_firestore/
│   ├── cloudbuild-test.yaml
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
├── tests/
│   └── __init__.py
├── .cloudignore
├── .gitignore
├── cloudbuild-bot.yaml
├── cloudbuild-collector.yaml
└── README.md
