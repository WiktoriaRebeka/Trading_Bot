Automated Trading Bot for Bybit (GCP)
Overview
This project is a professional-grade, automated trading system designed for scalping on the Bybit exchange. The system is fully event-driven (PUSH model) and deployed on Google Cloud Platform (GCP) for maximum scalability and resilience.
Unlike traditional retail bots, this system utilizes Market Microstructure (Order Flow) and Relative Strength (RS/RW) analysis via Sierra Chart, ensuring ultra-low latency and high-precision execution.

1. System Architecture (Event-Driven)
The architecture is split into two independent but tightly coupled environments:
A. Analytics Engine (Sierra Chart)
Located on a dedicated Windows VM in Tokyo (asia-northeast1) to achieve sub-millisecond proximity to Bybit’s matching engine.
Component
Responsibility
Sierra Chart (ACSIL)
Real-time Order Flow analysis, Liquidity Toxicity tracking, and Signal generation.
GCP Webhook (Cloud Function)
Acts as a secure gateway, receiving signals via HTTPS POST and forwarding them to the Execution Engine.

B. Execution Engine (Google Cloud - Serverless)
A fully serverless stack that processes signals and manages the trade lifecycle.
Component
Responsibility
Trading Bot Service (Cloud Run)
Core logic: receives PUSH alerts, calculates position size, and executes orders.
Firestore
Real-time state tracking (Active Orders, Instrument Rules, Klines).
BigQuery
Long-term storage for trade analytics and performance auditing.
Cloud Scheduler
Triggers maintenance tasks (PnL logging, Trailing Stop activation).


2. Infrastructure Security
To protect the Analytics Engine and trading capital, a Zero-Trust Security Model is implemented:
Windows VM Firewall (Sierra Chart)
RDP Access (Port 3389): Strictly restricted to specific Whitelisted IPs. All other traffic is dropped at the Google network edge.
Brute-Force Prevention: By closing the RDP port to the world, we eliminate unauthorized login attempts and preserve CPU resources.
API & Secret Management
GCP Secret Manager: Bybit API keys and Webhook tokens are never hardcoded; they are fetched at runtime.
Webhook Authentication: Every signal from Sierra Chart must include a secret_token validated by the receiver.

3. Core Execution Logic
Cloud Run Endpoints
Endpoint
Trigger
Responsibility
/process-alerts
PUSH (Immediate)
Validates signal and places LIMIT orders (Entry, SL, TP).
/update-orders
Scheduler (2 min)
Monitors PLACED orders, updates status to OPEN, and manages Trailing Stops.
/log-pnl
Scheduler (15 min)
Fetches closed trade data, logs to BigQuery, and cleans up Firestore.


4. Core Trading Concepts
Alpha Source
Signals are derived from Volume Absorption and Liquidity Toxicity (VPIN). The system enters when large players are trapped, rather than following lagging indicators.
Relative Strength Filter (RS/RW)
Every trade is validated against BTC. We only go LONG on coins showing strength against BTC and SHORT on those showing relative weakness.
Strict Risk Management
Fixed Risk: Exactly 2.5 USDT per trade.
Dynamic Sizing: Position size is calculated automatically based on the distance between Entry and Stop Loss, adjusted for fees.
Precision: All prices are rounded to the nearest Tick Size of the specific instrument.

5. Setup & Deployment
Configuration
Store Bybit API keys in Secret Manager.
Define instrument rules (tickSize, qtyStep) in Firestore (bot_config/instrument_rules).
Whitelist your local IP in GCP Firewall for RDP access.
Deployment
The system uses Cloud Build for automated CI/CD:
Bash
# Deploy the execution engine to GCP
gcloud builds submit --config cloudbuild-bot.yaml .

