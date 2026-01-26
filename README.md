Project Nexus: High-Frequency Market Structure Execution Engine
Overview

Project Nexus is a private, end-to-end automated trading infrastructure designed for low-latency execution on Bybit. The system leverages Sierra Chart (C++ ACSIL) for high-fidelity market microstructure analysis and Google Cloud Platform (Python) for serverless execution and dual-layer analytical logging.

This is a private execution engine, not a commercial service. It is optimized for capturing liquidity grabs and market structure shifts (BOS/CHOCH) using tick-level data.

System Architecture
code
Mermaid
download
content_copy
expand_less
graph TD
    subgraph "Detection Tier (Tokyo - asia-northeast1)"
        SC[Sierra Chart ACSIL C++] -->|JSON over HTTPS| CF
    end

    subgraph "Ingestion Tier (GCP - europe-central2)"
        CF[Cloud Function: Webhook Receiver] -->|Validate & Clean| FS[(Firestore: Alerts)]
        CF -->|Trigger| CR[Cloud Run: Bot Service]
    end

    subgraph "Execution & Analytics Tier"
        CR -->|REST API| BYB[Bybit Exchange]
        CR -->|State Management| FS_ACT[(Firestore: Active Orders)]
        CR -->|Signal Context| BQ_SIG[BigQuery: market_structure_signals]
        CR -->|Fill Data| BQ_TRD[BigQuery: real_trades_history]
    end

    BYB -->|PnL/Closed Trades| CR
1. Detection Tier: Market Structure Engine (C++)

The core signal logic resides in a custom ACSIL (Advanced Custom Study Interface and Language) study.

Logic: Monitors tick-by-tick data to identify Order Flow imbalances, Sweeps, and Market Structure Breaks.

Implementation: Built using C++ for maximum performance.

Connectivity: Utilizes sc.MakeHTTPPOSTRequest with n_ACSIL::s_HTTPHeader for compatibility with Sierra Chart Build 2860+.

Payload: Transmits a rich JSON object containing entry/SL/TP levels and microstructure context (e.g., Delta, RS/RW ratios).

2. Ingestion Tier: Secure Webhook (Python)

A hardened GCP Cloud Function acts as the gateway between the Windows-based detection environment and the Linux-based execution environment.

Security: Implements HMAC-SHA256/Secret Token validation to prevent unauthorized signal injection.

Data Sanitization: Explicitly handles C++ binary noise by stripping null bytes (\x00) and cleaning raw byte streams before JSON parsing.

Persistence: Archives every raw signal into Firestore for auditability before forwarding to the execution engine.

3. Execution & Analytics Tier (Python)

The Bot Service is a containerized Flask application deployed on GCP Cloud Run, designed for stateless, event-driven execution.

Execution Logic

Bybit Integration: Interfaces with Bybit V5 API. Uses Decimal precision for all financial calculations to eliminate floating-point errors.

Risk Management: Dynamic position sizing based on a fixed USDT risk model. Includes slippage buffers and taker-fee adjustments.

State Tracking: Uses Firestore to track PLACED, OPEN, and CLOSED states, enabling resilient Trailing Stop management via Cloud Scheduler.

Dual-Layer Analytical Logging

The system treats data as the primary asset, logging to BigQuery across two distinct tables:

market_structure_signals (The "Why"): Logs the microstructure context at the moment of the signal (e.g., M2 Delta, M5 Relative Strength). This allows for SQL-based backtesting of signal quality.

real_trades_history (The "Result"): Logs actual execution data, including average fill prices, realized RRR (Reward-to-Risk Ratio), and slippage.

Technical Highlights

Zero-Trust Security: API keys and secrets are managed via GCP Secret Manager. No credentials reside in the source code or environment variables.

Microstructure Context: Unlike standard bots, Nexus sends raw swing data and delta values to BigQuery, enabling post-trade analysis of whether a trade failed due to "bad logic" or "bad execution."

Precision Rounding: Implements round_price_by_tick and round_quantity_by_step to ensure 100% compliance with Bybit’s instrument-specific rules, preventing API retCode: 10001 errors.

Resilience: The update_filled_orders cycle ensures that even if a webhook is missed, the system synchronizes its state with the exchange within 120 seconds.

Deployment
Prerequisites

GCP Project with BigQuery, Firestore, and Cloud Run enabled.

Bybit API Keys (Mainnet or Testnet).

Sierra Chart installed on a low-latency VPS (Tokyo recommended for Bybit).

CI/CD Pipeline

Deployment is automated via Cloud Build:

code
Bash
download
content_copy
expand_less
# Deploy Bot Service
gcloud builds submit --config cloudbuild-bot.yaml .

# Deploy Data Collector
gcloud builds submit --config cloudbuild-collector.yaml .

Author: Senior HFT Systems Architect
Version: 3.2.0 (Stable)
License: Private / Proprietary
