Automated Trading Infrastructure – Sierra Chart → GCP → Bybit → BigQuery

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


2. Modular Microstructure Intelligence
The system is designed around core analytical modules. Currently implemented and logging to BigQuery:
Module 2: Order Flow Delta: Measures net aggression (Ask Vol - Bid Vol). Identifies when aggressive sellers are being absorbed by passive buyers.
Module 3: Stacking Imbalances: Detects diagonal aggressive pressure (e.g., buy-side dominance across multiple price levels).
Module 5: Relative Strength (RS/RW): Real-time correlation analysis against BTCUSDT.P. Filters for "Alpha" by identifying assets outperforming the market leader.


3. Infrastructure & Security
Zero-Trust Model: RDP access restricted to whitelisted IPs; all other traffic is dropped at the network edge.
Secret Management: Bybit API keys and Webhook tokens are managed via GCP Secret Manager (no hardcoded credentials).
Serverless Scalability: The execution layer scales to zero when inactive, minimizing costs while maintaining instant readiness for high-volatility events.


4. Core Trading Logic (Cloud Run Endpoints)
Endpoint	Trigger	Responsibility
/process-alerts	PUSH (Immediate)	Validates signals and prepares LIMIT orders with precise Tick-Size rounding.
/update-orders	Scheduler (2 min)	Manages PLACED orders, updates status to OPEN, and activates Trailing Stops.
/log-pnl	Scheduler (15 min)	Fetches closed trade data, calculates realized RRR, and audits performance in BigQuery.


5. BigQuery Logging & Data Strategy
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

6. Deployment
The system uses a fully automated CI/CD pipeline via Cloud Build:
code
Bash
# Deploy the execution engine
gcloud builds submit --config cloudbuild-bot.yaml .
