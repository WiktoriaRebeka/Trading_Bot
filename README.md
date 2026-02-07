
Automated Trading Infrastructure – Sierra Chart → GCP → Bybit → BigQuery

## Overview
This project is a professional-grade, high-frequency trading (HFT) infrastructure designed for the Bybit exchange. It utilizes a **PUSH-model architecture** to process Market Microstructure signals in real-time. The system is engineered to identify institutional order flow patterns, such as Passive Absorption and Aggressive Imbalances, providing a significant statistical edge over traditional technical analysis.


1. System Architecture (Event-Driven)
The architecture is split into two independent but tightly coupled environments:
A. Analytics Engine (Sierra Chart)
Location: Dedicated Windows VM in Tokyo (asia-northeast1) for sub-millisecond proximity to Bybit’s matching engine.
Transmitter (C++/ACSIL): Custom-built DLL performing real-time calculations on every tick. It detects triggers (Sweeps + Volume Aggression) and pushes a rich JSON payload (including new Time & Volatility Features) to the cloud.
Data Integrity: Operates on raw contract units to ensure precision across varying price levels.
B. Execution & Intelligence Engine (Google Cloud - Serverless)
GCP Webhook (Cloud Function): A secure, low-latency gateway that validates HMAC signatures and sanitizes incoming signals.
Bot Service (Cloud Run): The core logic. It validates signals, calculates position sizing based on risk parameters, manages instrument-specific rules via Firestore, handles order execution on Bybit, and logs outcomes.
BigQuery (Analytical Warehouse): Logs both signal context and execution outcomes for deep SQL-based backtesting and Win-Rate optimization.


2. Modular Microstructure Intelligence
The system is designed around core analytical modules, all of which are now enriched with Time and Volatility features sent from the C++ engine:
Module 1: Order Block & Structure Confirmation (BOS/CHOCH/OB): Confirms the structural context required for trade entry.
Module 2: Liquidity Analysis: Detects Equal Highs/Lows (EQH/EQL) and subsequent liquidity grabs, which serve as primary triggers.
Module 3: Time & Volatility Context (NEW): Enriches signals with Session, Minute of Day, Day of Week, Bar Range, OB Range, Swing Range, Distance to Liquidity, and Volatility Regime. Crucial for ML filtering.
Module 4: Relative Strength (RS/RW): Real-time correlation analysis against BTCUSDT.P (currently used for placeholder M2/M5 metrics).


3. Infrastructure & Security
Zero-Trust Model: RDP access restricted to whitelisted IPs; all other traffic is dropped at the network edge.
Secret Management: Bybit API keys and Webhook tokens are managed via GCP Secret Manager (no hardcoded credentials).
Serverless Scalability: The execution layer scales to zero when inactive, minimizing costs while maintaining instant readiness for high-volatility events.


4. Core Trading Logic (Cloud Run Endpoints)
Endpoint	Trigger	Responsibility
/process-alerts	PUSH (Immediate)	Validates signals, calculates position size using new features, and prepares LIMIT orders with precise Tick-Size rounding.
/update-orders	Scheduler (2 min)	Manages PLACED orders, updates status to OPEN, and activates Trailing Stops.
/log-pnl	Scheduler (15 min)	Fetches closed trade data, calculates realized RRR, and audits performance in BigQuery.


5. BigQuery Logging & Data Strategy
The system maintains a Single Source of Truth via two primary tables, now linked by event_id:
market_structure_signals: Logs the intent and market state (Context).
real_trades_history: Logs the outcome and execution quality (Result).


6. Deployment
The system uses a fully automated CI/CD pipeline via Cloud Build:
code
Bash
# Deploy the execution engine
gcloud builds submit --config cloudbuild-bot.yaml .