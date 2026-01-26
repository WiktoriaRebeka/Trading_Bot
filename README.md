Automated Trading Infrastructure – Sierra Chart → GCP → Bybit → BigQuery

This repository contains a private, institutional-grade trading system integrating tick-level signal detection in Sierra Chart (ACSIL C++), secure ingestion via GCP Cloud Functions, and real-time execution on Bybit through a hardened Cloud Run microservice. All analytics are logged to BigQuery for post-trade analysis.

No subscriptions. No external vendors. This is a closed execution engine.

Architecture Overview

Detection Tier – Sierra Chart ACSIL (C++)

Engine V3.1 detects:

Break of Structure (BOS)

Change of Character (CHOCH)

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
