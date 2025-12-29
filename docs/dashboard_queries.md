# Dashboard Query Examples

These SQL examples assume QuestDB and the `qe_events`, `qe_metrics`, `qe_exec` tables.

## Recent events (latest 200)

```sql
SELECT timestamp, symbol, event_type, reason_codes, component
FROM qe_events
ORDER BY timestamp DESC
LIMIT 200;
```

## Breaker frequency (last 60 minutes)

```sql
SELECT timestamp, symbol, breakers_active
FROM qe_metrics
WHERE timestamp > dateadd('m', -60, now())
  AND breakers_active IS NOT NULL
ORDER BY timestamp DESC;
```

## Reject reasons (top reasons, last 30 minutes)

```sql
SELECT timestamp, symbol, rejects_top
FROM qe_metrics
WHERE timestamp > dateadd('m', -30, now())
ORDER BY timestamp DESC;
```

## Inference p95 latency over time (5s buckets)

```sql
SELECT timestamp, avg(inference_p95_ms) AS p95
FROM qe_metrics
WHERE symbol='BTCUSDT'
  AND timestamp > dateadd('m', -30, now())
SAMPLE BY 5s ALIGN TO CALENDAR;
```

## Events by type (last 24h)

```sql
SELECT event_type, count() AS cnt
FROM qe_events
WHERE timestamp > dateadd('d', -1, now())
GROUP BY event_type
ORDER BY cnt DESC;
```

## Execution outcomes (if ledger ingested)

```sql
SELECT result, count() AS cnt
FROM qe_exec
WHERE timestamp > dateadd('d', -1, now())
GROUP BY result
ORDER BY cnt DESC;
```
