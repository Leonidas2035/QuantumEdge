# Supervisor Run History (Stage 1)

Each Supervisor run creates a durable run folder:

```
SupervisorAgent/runtime/runs/<run_id>/
  events.jsonl
  summary.json
  config_snapshot.json
  errors.log
```

`run_id` format (UTC):
`YYYYMMDD_HHMMSS_<gitsha7>` (or `..._nogit` if git is unavailable).

## Artifacts
- `events.jsonl`: structured timeline (RUN_START/RUN_END/ERROR + periodic status).
- `summary.json`: run metrics scaffold + breadcrumbs.
- `config_snapshot.json`: redacted effective config snapshot + hash.
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
   - `python SupervisorAgent/supervisor.py run-foreground`
2) Verify a new folder in:
   - `SupervisorAgent/runtime/runs/<run_id>/`
3) Confirm:
   - `events.jsonl` has RUN_START and RUN_END
   - `summary.json` exists
   - `config_snapshot.json` exists with secrets redacted
4) Check log rotation:
   - `SupervisorAgent/runtime/logs/supervisor.log` (+ rotated files)
