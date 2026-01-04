-- QuestDB schema for ILP ingestion and reporting.
-- Apply via QuestDB UI or /exec endpoint.

CREATE TABLE IF NOT EXISTS market_trades_raw (
  symbol SYMBOL,
  price DOUBLE,
  qty DOUBLE,
  side SYMBOL,
  trade_id LONG,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS market_l1 (
  symbol SYMBOL,
  bid DOUBLE,
  ask DOUBLE,
  bid_sz DOUBLE,
  ask_sz DOUBLE,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS bars_1s (
  symbol SYMBOL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  trades LONG,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS l2_fills (
  symbol SYMBOL,
  order_id SYMBOL,
  side SYMBOL,
  qty DOUBLE,
  price DOUBLE,
  fee DOUBLE,
  pnl DOUBLE,
  exchange SYMBOL,
  account SYMBOL,
  source SYMBOL,
  seq LONG,
  event_id STRING,
  payload_json STRING,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS l2_positions (
  symbol SYMBOL,
  side SYMBOL,
  qty DOUBLE,
  entry_price DOUBLE,
  mark_price DOUBLE,
  unrealized_pnl DOUBLE,
  leverage DOUBLE,
  margin DOUBLE,
  source SYMBOL,
  seq LONG,
  event_id STRING,
  payload_json STRING,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS l2_equity (
  equity DOUBLE,
  balance DOUBLE,
  available DOUBLE,
  currency SYMBOL,
  source SYMBOL,
  seq LONG,
  event_id STRING,
  payload_json STRING,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS l2_risk (
  risk_mode SYMBOL,
  max_dd DOUBLE,
  exposure DOUBLE,
  notes STRING,
  source SYMBOL,
  seq LONG,
  event_id STRING,
  payload_json STRING,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS bars_1m (
  symbol SYMBOL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  trades LONG,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS signals (
  bot_id SYMBOL,
  symbol SYMBOL,
  signal SYMBOL,
  score DOUBLE,
  model SYMBOL,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS orders (
  bot_id SYMBOL,
  symbol SYMBOL,
  side SYMBOL,
  type SYMBOL,
  qty DOUBLE,
  price DOUBLE,
  status SYMBOL,
  client_order_id SYMBOL,
  exchange_order_id SYMBOL,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS fills (
  bot_id SYMBOL,
  symbol SYMBOL,
  client_order_id SYMBOL,
  price DOUBLE,
  qty DOUBLE,
  fee DOUBLE,
  fee_asset SYMBOL,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS positions (
  bot_id SYMBOL,
  symbol SYMBOL,
  position DOUBLE,
  entry_price DOUBLE,
  unrealized_pnl DOUBLE,
  leverage DOUBLE,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS equity (
  bot_id SYMBOL,
  equity DOUBLE,
  balance DOUBLE,
  drawdown DOUBLE,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;

CREATE TABLE IF NOT EXISTS risk_events (
  bot_id SYMBOL,
  symbol SYMBOL,
  level SYMBOL,
  message STRING,
  ts TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY;
