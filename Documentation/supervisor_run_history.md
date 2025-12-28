# Supervisor Run History (Stage 1)

Each Supervisor run creates a durable run folder:

```
SupervisorAgent/runtime/runs/<run_id>/
  events.jsonl
  summary.json
  config_snapshot.json
  artifacts.json
  errors.log
```

`run_id` format (UTC):
`YYYYMMDD_HHMMSS_<gitsha7>` (or `..._nogit` if git is unavailable).

## Artifacts
- `events.jsonl`: structured timeline (RUN_START/RUN_END/ERROR + periodic status).
- `STAT_SNAPSHOT`: periodic stats snapshots.
- `TRADE_RESULT`: trade outcome events (if sent via API).
- `BLOCK_REASON`: risk guard block reasons.
- `SESSION_MARK`: episode/scenario tags.
- `summary.json`: run metrics scaffold + breadcrumbs.
- `config_snapshot.json`: redacted effective config snapshot + hash.
- `artifacts.json`: list of run artifacts + sizes.
- `errors.log`: crash/error breadcrumbs (if any).

## Breadcrumb fields (present in all files)
- run_id
- ts_utc
- git_commit / git_dirty
- config_hash
- policy_version / model_version
- supervisor_version
- host / platform

## Manual smoke check
1) Run Supervisor in foreground for ~30s:
   - `python SupervisorAgent/supervisor.py run-foreground --episode-set tick_scenarios_v1 --scenario-id S01`
2) Verify a new folder in:
   - `SupervisorAgent/runtime/runs/<run_id>/`
3) Confirm:
   - `events.jsonl` has RUN_START and RUN_END
   - `summary.json` exists
   - `config_snapshot.json` exists with secrets redacted
    - `artifacts.json` exists and lists files
4) Check log rotation:
   - `SupervisorAgent/runtime/logs/supervisor.log` (+ rotated files)

## Trade result intake (optional)
Send a trade outcome to the Supervisor API:
`POST /api/v1/telemetry/trade_result`
Body example:
```
{"symbol":"BTCUSDT","side":"BUY","qty":0.1,"entry_price":100,"exit_price":101,"pnl_realized":0.1,"fees":0.0,"reason_close":"TP"}
```
