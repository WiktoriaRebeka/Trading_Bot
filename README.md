Automated Trading Bot for Bybit on Google Cloud
Overview

This project is an automated, high-frequency trading system designed for scalping on the Bybit exchange.
The system is fully event-driven (PUSH model) and deployed on Google Cloud Platform (GCP) for scalability and resilience.
Sierra Chart is used as the analytics layer to achieve ultra-low-latency market analysis.

The system focuses on Market Microstructure (Order Flow) and Relative Strength (RS/RW) rather than traditional retail technical analysis, allowing faster and more reliable trade execution.

1. System Architecture (Event-Driven)

The architecture is split into two independent but tightly coupled environments:

Analytics Engine – signal generation

Execution Engine – trade execution and lifecycle management

A. Analytics Engine (Sierra Chart)


This component acts as the primary data source.
It runs on a dedicated Windows VM located close to Bybit servers to minimize latency.

| Component                           | Responsibility                                                            |
| ----------------------------------- | ------------------------------------------------------------------------- |
| **Webhook Receiver (Firestore)**    | Receives trading signals, stores them, and immediately triggers execution |
| **Trading Bot Service (Cloud Run)** | Core trading logic, order placement, and position management              |
| **Cloud Scheduler**                 | Triggers only monitoring and reporting tasks                              |


Signals are sent immediately after edge detection, without polling or batching.

B. Execution Engine (Google Cloud – Serverless)

The execution layer is built on Cloud Run, with Firestore used for state tracking and BigQuery for analytics.

| Endpoint              | Trigger Type                 | Responsibility                                                                 |
| --------------------- | ---------------------------- | ------------------------------------------------------------------------------ |
| **`/process-alerts`** | PUSH (event-driven)          | Immediately opens LIMIT orders (entry, SL, TP) on Bybit within milliseconds    |
| **`/update-orders`**  | Scheduler (every 2 minutes)  | Tracks order states (`PLACED → FILLED → OPEN`) and manages trailing stop logic |
| **`/log-pnl`**        | Scheduler (every 15 minutes) | Logs closed trades to BigQuery and cleans Firestore                            |


The system operates strictly in a PUSH-based flow for trade execution.

2. Core Execution Logic (Cloud Run Endpoints)

The trading-bot-service exposes three independent endpoints:

Endpoint	Trigger Type	Responsibility
/process-alerts	PUSH (event-driven)	Immediately opens LIMIT orders (entry, SL, TP) on Bybit within milliseconds
/update-orders	Scheduler (every 2 minutes)	Tracks order states (PLACED → FILLED → OPEN) and manages trailing stop logic
/log-pnl	Scheduler (every 15 minutes)	Logs closed trades to BigQuery and cleans Firestore
3. Core Trading Concepts

The system is designed around professional trading principles rather than retail indicators:

Alpha Source
Signals are generated using Volume Absorption and Liquidity Toxicity, not candlestick patterns.

Relative Strength Filter (RS/RW)
Every trade is validated against relative strength versus BTC to avoid trading against market dominance.

Strict Risk Control (risk_usdt)
Each trade risks exactly 2.5 USDT.
Position size is calculated dynamically based on Entry and Stop Loss.
Take Profit is defined in the range of 1.3R – 1.5R.

orderLinkId – Single Source of Truth
A unique identifier connects the full trade lifecycle:
Signal → Entry Order → SL/TP Orders → BigQuery Record.

4. Setup & Deployment
Configuration & Secrets

All environment variables and API keys are stored securely in GCP Secret Manager.

Deployment

The system is deployed automatically using Cloud Build, defined in cloudbuild-bot.yaml.

Conclusion

This system is built for speed, precision, and data quality.
By combining order-flow-based analytics, event-driven execution, and serverless cloud infrastructure, it provides a strong technical edge over traditional retail trading systems.