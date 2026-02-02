# Alerts

The alerts engine evaluates simple, rule-based conditions on the Supervisor dashboard summary and TSDB ingest lag.

## Rules

Rules are defined in `config/alerts.yaml`. Each rule has:

- `name`
- `severity` (`INFO`, `WARN`, `CRIT`)
- `field` (dot path in the summary payload)
- `operator` (`>=`, `<=`, `truthy`, etc.)
- `threshold`
- `duration_sec`
- `cooldown_sec`

Example:

```yaml
rules:
  - name: "tick_stale"
    severity: "WARN"
    field: "summary.tick_age_ms"
    operator: ">="
    threshold: 5000
    duration_sec: 30
    cooldown_sec: 120
```

## API

- `GET /api/v1/alerts/active`
- `GET /api/v1/alerts/recent?limit=200`
- `POST /api/v1/alerts/ack` `{ "alert_id": "...", "note": "..." }`
- `POST /api/v1/alerts/silence` `{ "rule": "tick_stale", "minutes": 60 }`

## Storage

Alerts are stored under `runtime/alerts/`:

- `alerts.jsonl` (history)
- `active.json` (current active)
- `silence.json` (silenced rules)
