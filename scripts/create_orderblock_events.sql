-- Tabela eventów MSI / OrderBlock (pełny schemat v2).
-- Uruchom w BigQuery Console lub: bq query --use_legacy_sql=false < create_orderblock_events.sql
-- Istniejąca tabela: scripts/alter_orderblock_events_v2.sql

CREATE TABLE IF NOT EXISTS `trading-bot-463318.trading_analytics.orderblock_events` (
  event_id STRING NOT NULL,
  event_type STRING NOT NULL,
  symbol STRING NOT NULL,
  event_ts TIMESTAMP NOT NULL,
  session STRING,
  minute_of_day INT64,
  day_of_week INT64,
  ob_id STRING,
  chain_id STRING,
  ob_formed_ts TIMESTAMP,
  ob_candle_ts TIMESTAMP,
  ob_high FLOAT64,
  ob_low FLOAT64,
  ob_height FLOAT64,
  ob_direction STRING,
  initial_trend STRING,
  hl_lh_level FLOAT64,
  bos_level FLOAT64,
  liquidity_level FLOAT64,
  is_first_ob BOOL,
  entry_limit FLOAT64,
  sl FLOAT64,
  risk_ob FLOAT64,
  matched_liq_volume FLOAT64,
  obi FLOAT64,
  funding_rate FLOAT64,
  dom_wall BOOL,
  delta FLOAT64,
  outcome STRING,
  realized_r FLOAT64,
  trade_order_id STRING,
  trade_event_id STRING,
  raw_context JSON
);
