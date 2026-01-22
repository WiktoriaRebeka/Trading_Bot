Automated Microstructure Trading & Data Engine (GCP)
Overview
This project is a professional-grade, high-frequency trading (HFT) infrastructure designed for the Bybit exchange. It utilizes a PUSH-model architecture to process Market Microstructure signals in real-time. The system is engineered to identify institutional order flow patterns, such as Passive Absorption and Aggressive Imbalances, providing a significant statistical edge over traditional technical analysis.
Key Differentiator: Unlike standard bots, this system captures and stores raw microstructure snapshots (9 modules) into BigQuery, building a proprietary dataset for Edge Discovery and future Data as a Service (DaaS) monetization.

1. System Architecture (Event-Driven)
A. Analytics Engine (Sierra Chart)
Location: Dedicated Windows VM in Tokyo (asia-northeast1) for sub-millisecond proximity to Bybit’s matching engine.
Transmitter (C++/ACSIL): Custom-built DLL that performs real-time calculations on every tick. It detects triggers (Sweeps + Volume) and pushes a rich JSON payload to the cloud.
Data Integrity: Operates on raw contract units (e.g., ETH quantity) to ensure precision across varying price levels.
B. Execution & Intelligence Engine (Google Cloud)
GCP Webhook (Cloud Function): A secure, low-latency gateway that validates and sanitizes incoming signals.
Bot Service (Cloud Run): The "Brain" of the system. It calculates position sizing based on a fixed risk model (2.5 USDT), manages instrument-specific rules via Firestore, and handles the trade lifecycle.
BigQuery (Analytical Warehouse): Every signal is logged with its full Microstructure Context (JSON format), allowing for deep SQL-based backtesting and Win-Rate optimization.

2. Modular Microstructure Intelligence
The system is designed around 9 core analytical modules. Currently implemented and logging to BigQuery:
Module 2: Order Flow Delta: Measures net aggression (Ask Vol - Bid Vol). Identifies when aggressive sellers are being absorbed by passive buyers.
Module 3: Stacking Imbalances: Detects diagonal aggressive pressure (e.g., 300% buy-side dominance across multiple price levels).
Module 5: Relative Strength (RS/RW): Real-time correlation analysis against BTCUSDT.P. Filters for "Alpha" by identifying assets outperforming the market leader.

3. Infrastructure & Security
Zero-Trust Model: RDP access restricted to whitelisted IPs; all other traffic dropped at the edge.
Secret Management: API keys and Webhook tokens are managed via GCP Secret Manager (no hardcoded credentials).
Serverless Scalability: The execution layer scales to zero when inactive, minimizing costs while maintaining instant readiness for high-volatility events.

4. Core Trading Logic (Cloud Run Endpoints)
Endpoint
Trigger
Responsibility
/process-alerts
PUSH (Immediate)
Validates signals and prepares LIMIT orders with precise Tick-Size rounding.
/update-orders
Scheduler (2 min)
Manages PLACED orders, updates status to OPEN, and activates Trailing Stops.
/log-pnl
Scheduler (15 min)
Fetches closed trade data, calculates realized RRR, and audits performance in BigQuery.


5. Roadmap: From Bot to DaaS
Phase 1 (Current): Data Collection & Dry Run. Verifying the integrity of the 9 microstructure modules.
Phase 2: Edge Discovery. Using BigQuery ML and SQL to isolate high-probability signal clusters.
Phase 3: Live Execution. Transitioning from Dry Run to active capital management on Bybit.
Phase 4: DaaS Launch. Exposing processed microstructure features (Z-Scores, Percentiles) via a commercial API, utilizing "Recipe Protection" to share insights without revealing the underlying strategy.

6. Deployment
The system uses a fully automated CI/CD pipeline via Cloud Build:
code Bash
downloadcontent_copy
expand_less
# Deploy the execution engine
gcloud builds submit --config cloudbuild-bot.yaml .



