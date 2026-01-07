# TSDB Data Model (QuestDB)

This document defines the data contract for time-series storage. The goal is to keep raw data minimal, prefer aggregates for dashboards/ML, and retain only what is needed for operational oversight.

## Event layers

- L0 raw (optional): raw trades only when explicitly enabled.
- L1 normalized: top-of-book (L1) and time bars (1s/1m).
- L2 telemetry: signals, orders, fills, positions, equity, and risk events.

We do NOT store full orderbook deltas by default. Enable only if required for specific research.

## Table catalog

### market_trades_raw (L0, optional)
- Columns: symbol SYMBOL, price DOUBLE, qty DOUBLE, side SYMBOL, trade_id LONG, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for symbol/side
- Expected write rate: bursty, per-symbol trades (low to high depending on venue)

### market_l1 (L1)
- Columns: symbol SYMBOL, bid DOUBLE, ask DOUBLE, bid_sz DOUBLE, ask_sz DOUBLE, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL column for symbol
- Expected write rate: 1-20 rows/sec per symbol (stream-driven)

### microstructure_v1 (L1)
- Columns: symbol SYMBOL, best_bid_px DOUBLE, best_bid_qty DOUBLE, best_ask_px DOUBLE, best_ask_qty DOUBLE,
  ofi_raw DOUBLE, ofi_z DOUBLE, ofi_ma5 DOUBLE, spread_bps DOUBLE, top_qty_sum DOUBLE,
  trade_rate_1s DOUBLE, volume_1s DOUBLE, is_gap BOOLEAN, is_resynced BOOLEAN, ts_event LONG, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL column for symbol
- Expected write rate: 1-20 rows/sec per symbol (stream-driven)

### bars_1s (L1)
- Columns: symbol SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, trades LONG, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL column for symbol
- Expected write rate: 1 row/sec per symbol

### bars_1m (L1)
- Columns: symbol SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, trades LONG, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL column for symbol
- Expected write rate: 1 row/min per symbol

### signals (L2)
- Columns: bot_id SYMBOL, symbol SYMBOL, signal SYMBOL, score DOUBLE, model SYMBOL, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for bot_id and symbol
- Expected write rate: low (decision frequency)

### orders (L2)
- Columns: bot_id SYMBOL, symbol SYMBOL, side SYMBOL, type SYMBOL, qty DOUBLE, price DOUBLE, status SYMBOL,
  client_order_id SYMBOL, exchange_order_id SYMBOL, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for bot_id and symbol; avoid large free-form strings
- Expected write rate: low to moderate (order decisions)

### fills (L2)
- Columns: bot_id SYMBOL, symbol SYMBOL, client_order_id SYMBOL, price DOUBLE, qty DOUBLE, fee DOUBLE, fee_asset SYMBOL, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for bot_id and symbol
- Expected write rate: low to moderate (order execution)

### positions (L2)
- Columns: bot_id SYMBOL, symbol SYMBOL, position DOUBLE, entry_price DOUBLE, unrealized_pnl DOUBLE, leverage DOUBLE, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for bot_id and symbol
- Expected write rate: low (periodic snapshots)

### equity (L2)
- Columns: bot_id SYMBOL, equity DOUBLE, balance DOUBLE, drawdown DOUBLE, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL column for bot_id
- Expected write rate: low (periodic snapshots)

### risk_events (L2)
- Columns: bot_id SYMBOL, symbol SYMBOL, level SYMBOL, message STRING, ts TIMESTAMP
- Timestamp: ts
- Partition: DAY
- Index strategy: SYMBOL columns for bot_id and symbol; keep message short
- Expected write rate: low (alerts only)

## Retention policy (initial targets)

- L0 raw trades: 7-30 days (optional, only if enabled)
- L1 bars/topbook: 180+ days
- L2 telemetry: 180+ days

Retention is enforced by scheduled maintenance and partition drops. Tables can use different policies per group.

## Notes

- Keep payloads minimal and avoid storing large blobs or full orderbook deltas.
- Prefer SYMBOL for high-cardinality filters (symbol, bot_id) and keep strings short in hot tables.
- Use aggregated bars for most analytics; raw trades are optional and short-lived.
