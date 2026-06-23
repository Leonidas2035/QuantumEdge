# Telemetry Ledger (events.jsonl)

SupervisorAgent writes structured JSONL events to `logs/events_YYYY-MM-DD.jsonl`.
Schema version: `telemetry.v1`.

## Event schema (telemetry.v1)

Each line is a JSON object with:

- `ts_ms`: epoch milliseconds
- `schema_version`: `"telemetry.v1"`
- `component`: `"supervisor"`, `"hub"`, or `bot:<name>`
- `event_type`: string (ex: `PROCESS_START`, `BOT_STOP`, `API_CALL`)
- `severity`: `"INFO" | "WARN" | "ERROR"`
- `run_id`: unique id for this SupervisorAgent run
- `trace_id`: optional request trace id (API requests)
- `fields`: event-specific dictionary

## run_id / trace_id

- `run_id` is generated once at SupervisorAgent start and injected into child
  process environments as `RUN_ID`.
- `trace_id` is generated per API request and attached to `API_CALL` events.

## Tail API

Endpoint:

`GET /api/v1/events/tail?limit=200&types=PROCESS_START,API_CALL&since_ts_ms=...`

Returns newest-first (bounded) events.

## Retention

Configured in `config/supervisor.yaml`:

```yaml
events_retention_days: 7
```

Old `events_*.jsonl` files beyond the retention window are removed on startup.
