-- QuestDB schema v1 for QuantumEdge telemetry.

CREATE TABLE IF NOT EXISTS qe_events (
  timestamp TIMESTAMP,
  symbol SYMBOL,
  mode SYMBOL,
  component SYMBOL,
  event_type SYMBOL,
  reason_codes STRING,
  latency_ms DOUBLE,
  payload_json STRING,
  run_id STRING,
  event_hash STRING
) TIMESTAMP(timestamp) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS qe_metrics (
  timestamp TIMESTAMP,
  symbol SYMBOL,
  mode SYMBOL,
  tick_age_ms LONG,
  book_age_ms LONG,
  breakers_active STRING,
  rejects_top STRING,
  inference_p50_ms DOUBLE,
  inference_p95_ms DOUBLE,
  position_notional DOUBLE,
  policy_id STRING,
  schema_hash STRING,
  error_code STRING,
  payload_json STRING
) TIMESTAMP(timestamp) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS qe_exec (
  timestamp TIMESTAMP,
  symbol SYMBOL,
  side SYMBOL,
  order_type SYMBOL,
  qty DOUBLE,
  price DOUBLE,
  slippage_bps DOUBLE,
  fee_bps DOUBLE,
  result SYMBOL,
  client_order_id STRING,
  exchange_order_id STRING,
  payload_json STRING
) TIMESTAMP(timestamp) PARTITION BY DAY;
