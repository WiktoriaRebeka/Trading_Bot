Oczywiście. Masz absolutną rację – po tak gruntownej refaktoryzacji dokumentacja musi zostać zaktualizowana, aby odzwierciedlała nową, ulepszoną architekturę.

Poniżej znajduje się poprawiona wersja pliku README.md. Wprowadziłem zmiany, które precyzyjnie opisują nowy, wydajniejszy i bezpieczniejszy sposób działania systemu, w tym:

Zaktualizowany przepływ danych (bot korzysta z cache'u).

Dynamiczną konfigurację symboli.

Bezpieczne zarządzanie sekretami.

Zrównoleglone pobieranie danych.

Nową strukturę plików z models.py i config_loader.py.

Możesz po prostu skopiować i wkleić całą poniższą zawartość do swojego pliku README.md.

Automated Trading Bot v7.0 (GCP)
1. Project Description and Main Goal

The Automated Trading Bot v7.0 is a fully automated, serverless trading system running 24/7 on the Google Cloud Platform (GCP). Its primary objective is the autonomous execution and advanced analysis of a trading strategy based on the "Smart Money" concept, specifically targeting Order Block (OB) formations.

The system is designed for maximum reliability, security, and performance by decoupling data collection from the core trading logic. It aims to minimize operational costs and ensure analytical precision through a robust, multi-component serverless architecture, where all custom logic is deployed as containerized services on Cloud Run.

2. Operational Cycle and Core Business Logic

The system operates based on a precisely defined, multi-stage data and decision flow:

Stage 1: Signal Generation (TradingView)

Source: A custom Pine Script indicator on the TradingView platform.

Indicator Logic: The indicator analyzes the price chart in real-time for New OB (Order Block) formations.

Action: Upon identifying a new, valid OB, the indicator triggers an alert via a webhook.

Stage 2: Alert Ingestion and Queuing (GCP)

Service: A lightweight Cloud Function (gcp-webhook) acting as a webhook endpoint.

Flow:

The webhook from TradingView sends a POST request with the alert data (JSON) to the function's public URL.

The function immediately saves the raw alert as a new document in the alerts collection in Cloud Firestore, which serves as a persistent input queue.

Stage 3: High-Performance Data Collection (Independent Microservice on Cloud Run)

To ensure reliability and mitigate API latency, a dedicated data collection service runs independently and asynchronously.

Service: A containerized Python application (data-collector-service) on Cloud Run, triggered every minute by Cloud Scheduler.

Flow:

The service reads a dynamic list of symbols to monitor from a configuration document in Firestore (bot_config/symbols_config). This allows for live updates without redeploying the code.

It queries the Bybit API for the latest 1-minute kline data for all symbols in parallel (asynchronously), drastically reducing collection time.

It saves the high, low, and close prices for each symbol into a dedicated Firestore collection: latest_klines. This collection acts as a fast, reliable, internal data cache.

Stage 4: Cyclical Processing and Execution (Main Bot on Cloud Run)

Orchestration: A separate Cloud Scheduler job invokes the main bot service (trading-bot-service) every minute.

Main Bot Flow:

4.1. Setup Management

The bot reads new alerts from the alerts queue and validates them using Pydantic models. Each new alert for a given symbol overwrites the previous "active setup" in the active_setups collection, invalidating the Old OB.

4.2. Opening New Positions

Data Source: The bot reads the latest price data for all monitored symbols directly from the latest_klines cache in Firestore. It no longer queries the Bybit API, making the process faster and more fault-tolerant.

Decision Logic: It iterates through all active setups and, based on the cached high and low of the latest candle, decides whether to open a new position.

State Management: It correctly identifies Fresh OB (first entry) vs. Used OB (subsequent entries) and handles the price reset logic after a LOSE. Upon entry, it creates an isolated document in the open_trades collection, "freezing" all transaction parameters (SL, TP, etc.).

4.3. Monitoring Open Positions

The bot iterates through all documents in the open_trades collection. It uses the fresh kline data from the Firestore cache to check if any position's "frozen" SL or TP has been breached.

4.4. Finalization and Optimized Post-Mortem Analysis

Initial Log to BigQuery: When a position is closed (WIN/LOSE), the bot fetches the historical kline data for the trade's duration, calculates the maximum achieved R:R, and writes a complete, initial record to BigQuery.

"Ghost" Creation: A "ghost" of the closed trade is created in the analyzed_trades collection in Firestore, storing its initial state.

Efficient Passive Tracking: In subsequent cycles, the bot optimally tracks this ghost. Instead of re-fetching the entire trade history, it only fetches new klines since the last check. If the price reaches a new, higher TP level, it sends a secure, parameterized UPDATE query to BigQuery to enrich the existing record. This continues until tp_5_0 or the original sl is hit, at which point the ghost is deleted.

3. Technical Architecture

The system employs a decoupled, secure, and high-performance microservice architecture on GCP.

Data Ingestion: TradingView (Webhook) -> Cloud Function -> Cloud Firestore (collection alerts)

Data Collection (Cache): Cloud Scheduler -> Cloud Run (data-collector-service) -> Bybit API -> Cloud Firestore (collection latest_klines)

Orchestration: Cloud Scheduler (1-minute cron triggers for both services).

Application Core: Cloud Run (Docker containers for trading-bot-service and data-collector-service).

Security:

GCP Secret Manager: All sensitive configurations (like API keys) are stored securely and accessed via IAM roles, not in code or .env files in production.

Parameterized Queries: All UPDATE operations on BigQuery are parameterized to prevent SQL Injection vulnerabilities.

State Management (Firestore):

bot_config: Stores dynamic application configuration, such as the list of symbols to watch.

alerts: Persistent queue for incoming signals.

latest_klines: Real-time price cache, updated by the collector.

active_setups: Stores the latest, active setup for each symbol.

open_trades: Stores the state of each individual, open position.

analyzed_trades: Stores data of closed positions for passive, optimized post-mortem analysis.

Analytics & Logging:

Cloud Logging: Central hub for real-time monitoring, with structured log names for easy filtering.

BigQuery: Analytical data warehouse for all closed trades.

Network Infrastructure:

Serverless VPC Access Connector & Cloud NAT: Provide a static IP address for all outbound traffic.

Deployment Automation (CI/CD):

GitHub Actions: Separate workflows for each service, triggered by changes on the main branch.

4. Key Design Principles

Decoupling: Data collection is fully separated from trading logic. The main bot is resilient to API failures as it relies on an internal cache.

Security First: Secrets are managed by GCP Secret Manager, and database queries are secured against injection attacks.

High Performance: Asynchronous, parallel data fetching in the collector minimizes latency.

Robustness & Fault Tolerance: The application is written defensively, using Pydantic models for data validation and handling API errors gracefully.

Flexibility: The list of traded symbols can be managed dynamically in Firestore without code changes.

5. Project Structure

The project is organized into a main application directory and a separate directory for the webhook function.

Generated code
.
├── .github/
│   └── workflows/
│       ├── deploy-collector.yml
│       └── deploy.yml
├── gcp-webhook/
│   ├── main.py
│   └── requirements.txt
├── .env
├── .gitignore
├── Dockerfile
├── Dockerfile.bot
├── README.md
├── requirements.txt
├── bot_logic.py
├── bigquery_logger.py
├── collector_main.py
├── config_loader.py      # New: Handles loading config from Secret Manager or .env
├── constants.py
├── data_collector.py
├── fetch_from_firestore.py
├── firebase_client.py
├── main.py
├── models.py             # New: Pydantic models for data validation and structure
└── state_manager.py

Key File Descriptions:

main.py: The main entry point (Flask) for the trading-bot-service.

bot_logic.py: The brain of the system, containing the core business logic.

collector_main.py: The Flask entry point for the data-collector-service.

data_collector.py: The core logic for the data collector, performing asynchronous data fetching.

config_loader.py: A dedicated module for loading configuration securely from GCP Secret Manager in the cloud or from a local .env file.

models.py: Defines Pydantic data models (AlertData, OpenTradeData, etc.) to ensure data integrity and improve code readability.

state_manager.py: A module abstracting all state management operations on Firestore.

bigquery_logger.py: The module responsible for all secure communication with BigQuery.

constants.py: A central place for constants and configuration parameters (read from environment variables).

gcp-webhook/main.py: The isolated code for the alert ingestion Cloud Function.

Dockerfile / Dockerfile.bot: Definitions for building the container images.

.github/workflows/*.yml: CI/CD pipeline definitions for automated deployment.