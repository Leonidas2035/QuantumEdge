# QuestDB Queries (TSDB)

These queries target the ILP schema in `deploy/questdb/schema.sql`.

## Equity curve per bot (bucketed)

```sql
SELECT bot_id, ts, avg(equity) AS equity
FROM equity
WHERE ts >= '2025-01-01T00:00:00Z'
SAMPLE BY 5m ALIGN TO CALENDAR;
```

## PnL per symbol (orders + fills join)

```sql
SELECT o.symbol,
       sum(CASE WHEN o.side='SELL' THEN f.price * f.qty ELSE -f.price * f.qty END) AS pnl_gross
FROM fills f
JOIN orders o ON f.client_order_id = o.client_order_id AND f.symbol = o.symbol
WHERE f.ts >= '2025-01-01T00:00:00Z'
GROUP BY o.symbol;
```

## Order / fill counts (bucketed)

```sql
SELECT bot_id, symbol, count() AS orders
FROM orders
WHERE ts >= '2025-01-01T00:00:00Z'
SAMPLE BY 1h ALIGN TO CALENDAR;

SELECT bot_id, symbol, count() AS fills
FROM fills
WHERE ts >= '2025-01-01T00:00:00Z'
SAMPLE BY 1h ALIGN TO CALENDAR;
```

## Risk events counts

```sql
SELECT bot_id, symbol, level, count() AS events
FROM risk_events
WHERE ts >= '2025-01-01T00:00:00Z'
GROUP BY bot_id, symbol, level;
```

## Optional ASOF join (fills with L1 context)

```sql
SELECT f.symbol, f.ts, f.price, l1.bid, l1.ask
FROM fills f ASOF JOIN market_l1 l1
ON f.symbol = l1.symbol
WHERE f.ts >= '2025-01-01T00:00:00Z';
```

## Latency stats (if metrics table exists)

```sql
SELECT symbol, ts,
       avg(inference_p50_ms) AS inference_p50_ms,
       avg(inference_p95_ms) AS inference_p95_ms
FROM qe_metrics
WHERE ts >= '2025-01-01T00:00:00Z'
SAMPLE BY 5m ALIGN TO CALENDAR;
```
