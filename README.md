Automated Trading Infrastructure – Sierra Chart → GCP → Bybit → BigQuery
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD
## Overview
This project is a professional-grade, high-frequency trading (HFT) infrastructure designed for the Bybit exchange. It utilizes a **PUSH-model architecture** to process Market Microstructure signals in real-time. The system is engineered to identify institutional order flow patterns, such as Passive Absorption and Aggressive Imbalances, providing a significant statistical edge over traditional technical analysis.

## System Architecture

```mermaid
graph TD
    subgraph "Detection Tier (Tokyo - asia-northeast1)"
        SC[Sierra Chart C++/ACSIL] -->|POST JSON| CF[GCP Cloud Function]
    end

    subgraph "Ingestion Tier (europe-central2)"
        CF -->|Auth & Clean| FS_ALERTS[(Firestore: Alerts)]
        CF -->|Trigger| CR[GCP Cloud Run: Bot Service]
    end
    
    subgraph "Execution & Intelligence Engine"
        CR -->|Place Order| BYBIT[Bybit API]
        CR -->|Manage State| FS_ORDERS[(Firestore: Active Orders)]
        CR -->|Log Signal Context| BQ_SIG[BigQuery: market_structure_signals]
        CR -->|Log Execution Data| BQ_TRD[BigQuery: real_trades_history]
    end

    BYBIT -->|PnL & Fill Data| CR



1. System Architecture (Event-Driven)
A. Analytics Engine (Sierra Chart)
Location: Dedicated Windows VM in Tokyo (asia-northeast1) for sub-millisecond proximity to Bybit’s matching engine.
Transmitter (C++/ACSIL): Custom-built DLL performing real-time calculations on every tick. It detects triggers (Sweeps + Volume Aggression) and pushes a rich JSON payload to the cloud.
Data Integrity: Operates on raw contract units to ensure precision across varying price levels.
B. Execution & Intelligence Engine (Google Cloud)
GCP Webhook (Cloud Function): A secure, low-latency gateway that validates HMAC signatures and sanitizes incoming signals (stripping C++ binary noise).
Bot Service (Cloud Run): The "Brain" of the system. It calculates position sizing based on a fixed risk model, manages instrument-specific rules via Firestore, and handles the trade lifecycle.
BigQuery (Analytical Warehouse): Every signal is logged with its full Microstructure Context, allowing for deep SQL-based backtesting and Win-Rate optimization.
=======
This repository contains a private, institutional-grade trading system integrating tick-level signal detection in Sierra Chart (ACSIL C++), secure ingestion via GCP Cloud Functions, and real-time execution on Bybit through a hardened Cloud Run microservice. All analytics are logged to BigQuery for post-trade analysis.
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD

2. Modular Microstructure Intelligence
The system is designed around core analytical modules. Currently implemented and logging to BigQuery:
Module 2: Order Flow Delta: Measures net aggression (Ask Vol - Bid Vol). Identifies when aggressive sellers are being absorbed by passive buyers.
Module 3: Stacking Imbalances: Detects diagonal aggressive pressure (e.g., buy-side dominance across multiple price levels).
Module 5: Relative Strength (RS/RW): Real-time correlation analysis against BTCUSDT.P. Filters for "Alpha" by identifying assets outperforming the market leader.
=======
No subscriptions. No external vendors. This is a closed execution engine.
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD

3. Infrastructure & Security
Zero-Trust Model: RDP access restricted to whitelisted IPs; all other traffic is dropped at the network edge.
Secret Management: Bybit API keys and Webhook tokens are managed via GCP Secret Manager (no hardcoded credentials).
Serverless Scalability: The execution layer scales to zero when inactive, minimizing costs while maintaining instant readiness for high-volatility events.
=======
Architecture Overview
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD

4. Core Trading Logic (Cloud Run Endpoints)
Endpoint	Trigger	Responsibility
/process-alerts	PUSH (Immediate)	Validates signals and prepares LIMIT orders with precise Tick-Size rounding.
/update-orders	Scheduler (2 min)	Manages PLACED orders, updates status to OPEN, and activates Trailing Stops.
/log-pnl	Scheduler (15 min)	Fetches closed trade data, calculates realized RRR, and audits performance in BigQuery.
=======
Detection Tier – Sierra Chart ACSIL (C++)
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD
5. Repository Structure
├── bot_service/                # Cloud Run Execution Engine
│   ├── app_setup.py            # Flask app & endpoint registration
│   ├── bot_logic.py            # Signal handling, QTY calculation, Dry-run logic
│   ├── bybit_executor.py       # Signed API integration (HMAC SHA-256)
│   ├── bigquery_logger.py      # Dual-layer logging logic
│   ├── state_manager.py        # Firestore-backed order state machine
│   ├── pnl_logger_real.py      # Closed position PnL & BQ transformation
│   ├── fetch_from_firestore.py # Timestamp synchronization
│   └── Dockerfile              # Container configuration
├── collector_service/          # Tick-Level Data Collector
│   ├── collector_main.py       # Entry point
│   ├── data_collector.py       # Kline/Historical data ingestion
│   └── Dockerfile              # Container configuration
├── gcp-webhook/                # Cloud Function Receiver
│   └── main.py                 # Secure webhook handler & data cleaning
├── shared_lib/                 # Shared Utilities & Business Logic
│   ├── models.py               # Pydantic alert schema validation
│   ├── firebase_client.py      # Firestore client & config fetching
│   ├── risk_manager.py         # Position sizing (Risk + Fees + Slippage)
│   ├── secret_manager.py       # GCP Secret Manager integration
│   ├── constants.py            # Global constants
│   └── config.py               # Environment configuration
├── cloudbuild-bot.yaml         # CI/CD for Bot Service
├── cloudbuild-collector.yaml   # CI/CD for Data Collector
└── README.md                   # Documentation
=======
Engine V3.1 detects:
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD
=======
Break of Structure (BOS)
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD
6. BigQuery Logging & Data Strategy
The system maintains a Single Source of Truth via two primary tables:
market_structure_signals
Logs the intent and market state:
Symbol, direction, and planned Entry/SL/TP.
Microstructure Context: M2 Delta, M5 RS Ratio.
Timestamp and raw Alert ID for traceability.
real_trades_history
Logs the outcome and execution quality:
Actual fill prices and slippage.
Net PnL, commissions, and realized RRR.
Exit type (TakeProfit, StopLoss, or TrailingStop).
=======
Change of Character (CHOCH)
>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb

<<<<<<< HEAD
7. Deployment
The system uses a fully automated CI/CD pipeline via Cloud Build:
code
Bash
# Deploy the execution engine
gcloud builds submit --config cloudbuild-bot.yaml .
=======
Liquidity Grabs (LG)

Swing context: M2 delta, M5 RS ratio

Uses n_ACSIL::s_HTTPHeader for Build 2860 compatibility

Sends structured alerts via HTTP POST to GCP

Ingestion Tier – GCP Cloud Functions

Validates HMAC/secret tokens

Cleans C++ binary noise

Archives raw alerts in Firestore

Forwards normalized payloads to Cloud Run

Execution & Analytics Tier – GCP Cloud Run (Python)

Places orders on Bybit (limit with SL/TP)

Manages position lifecycle

Activates trailing stops

Logs:

Microstructure signals → market_structure_signals

Real trade fills → real_trades_history

BigQuery acts as the Single Source of Truth for both strategy intent and execution outcome.

Mermaid Diagram – Full Pipeline

flowchart LR
    SC[Sierra Chart<br/>ACSIL Engine V3.1] -->|HTTP POST| CF[GCP Cloud Function<br/>Webhook Receiver]
    CF -->|Validated Payload| FS[Firestore<br/>Raw Archive]
    CF -->|Cleaned Alert| CR[Cloud Run Bot Service]
    CR -->|Order Execution| BYB[Bybit API]
    CR -->|Signal Log| BQ1[(BigQuery<br/>market_structure_signals)]
    CR -->|Trade Log| BQ2[(BigQuery<br/>real_trades_history)]

Repository Structure

bot_service/ – Cloud Run Execution Engine

app_setup.py – Flask app + endpoint registration

bot_logic.py – Signal handling, dry-run logic, QTY calculation

bybit_executor.py – Signed API integration with Bybit

bigquery_logger.py – Dual-layer logging

state_manager.py – Firestore-backed order state machine

pnl_logger_real.py – Closed position PnL logging

fetch_from_firestore.py – Timestamp sync

Dockerfile – Container for Cloud Run

collector_service/ – Tick-Level Data Collector

collector_main.py, data_collector.py – Historical data ingestion

Dockerfile – Containerized service

gcp-webhook/ – Cloud Function Receiver

main.py – Secure webhook handler

requirements.txt – Dependencies

shared_lib/ – Shared Utilities

models.py – Alert schema validation

firebase_client.py – Firestore access

risk_manager.py – Position sizing logic

secret_manager.py – GCP Secret Manager integration

constants.py, config.py – Configuration

Root Directory

.gitignore

README.md

cloudbuild-bot.yaml, cloudbuild-collector.yaml – CI/CD pipelines

Bybit Integration

API keys managed via GCP Secret Manager

Injected into Cloud Run via environment variables

All requests signed with HMAC SHA-256

BigQuery Logging

market_structure_signals

Logs signal intent:

Symbol, direction, entry/SL/TP

Microstructure context (M2 delta, M5 RS ratio)

Timestamp, raw alert ID

real_trades_history

Logs execution outcome:

Fill prices, slippage, PnL

Trailing stop activation

Emergency closures

Together, these enable:

Win-rate analysis

Slippage modeling

Strategy iteration via SQL

Operational Guarantees

Stateless execution engine

Deterministic signal ingestion

Firestore-backed state tracking

BigQuery-backed analytics

No external dependencies beyond Sierra Chart, GCP, and Bybit

Next Steps

Tick-level slippage modeling

Multi-symbol parallel execution

Latency benchmarking (SC → GCP → Bybit)

Reinforced microstructure context logging

For onboarding, schema specs, or production hardening checklists, reach out to the system architect.

>>>>>>> b036fcc22ef531a24698254e21fcc66f449c55eb