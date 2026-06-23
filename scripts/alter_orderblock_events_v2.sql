-- Rozszerzenie orderblock_events (krok 4 — OB + orderflow + outcome).
-- Uruchom w BigQuery Console po create_orderblock_events.sql.

ALTER TABLE `trading-bot-463318.trading_analytics.orderblock_events`
  ADD COLUMN IF NOT EXISTS ob_id STRING,
  ADD COLUMN IF NOT EXISTS entry_limit FLOAT64,
  ADD COLUMN IF NOT EXISTS sl FLOAT64,
  ADD COLUMN IF NOT EXISTS risk_ob FLOAT64,
  ADD COLUMN IF NOT EXISTS matched_liq_volume FLOAT64,
  ADD COLUMN IF NOT EXISTS obi FLOAT64,
  ADD COLUMN IF NOT EXISTS funding_rate FLOAT64,
  ADD COLUMN IF NOT EXISTS dom_wall BOOL,
  ADD COLUMN IF NOT EXISTS delta FLOAT64,
  ADD COLUMN IF NOT EXISTS outcome STRING,
  ADD COLUMN IF NOT EXISTS realized_r FLOAT64,
  ADD COLUMN IF NOT EXISTS trade_order_id STRING,
  ADD COLUMN IF NOT EXISTS trade_event_id STRING;
