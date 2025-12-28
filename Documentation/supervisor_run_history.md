# Supervisor Run History (Stage 1)

Each Supervisor run creates a durable run folder:

```
SupervisorAgent/runtime/runs/<run_id>/
  events.jsonl
  summary.json
  config_snapshot.json
  artifacts.json
  action_ledger.jsonl
  directives.json
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
- `ACTION_PROPOSED` / `ACTION_APPLIED` / `ACTION_REJECTED`: directive ledger entries.
- `DIRECTIVES_UPDATED`: directives snapshot changed.
- `summary.json`: run metrics scaffold + breadcrumbs.
- `config_snapshot.json`: redacted effective config snapshot + hash.
- `artifacts.json`: list of run artifacts + sizes.
- `action_ledger.jsonl`: append-only Supervisor directives + outcomes.
- `directives.json`: latest bot-control directives for this run.
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

## Offline Episode Engine
Offline episode tooling replays tick scenarios through the Supervisor decision core.

Scenario library:
- `SupervisorAgent/episodes/scenarios_v1.yaml`

Commands:
1) Cut episodes:
   - `python SupervisorAgent/supervisor.py episodes-cut --episode-set smoke_v1 --ticks-path data/ticks --format jsonl`
   - If no tick file exists yet: add `--synthetic` to generate a tiny JSONL sample.
2) Run episodes:
   - `python SupervisorAgent/supervisor.py episodes-run --episode-set smoke_v1 --episodes-manifest SupervisorAgent/runtime/episodes/smoke_v1/episodes_manifest.json`
3) Report:
   - `python SupervisorAgent/supervisor.py episodes-report --episode-set smoke_v1`

Artifacts:
- Episodes: `SupervisorAgent/runtime/episodes/<episode_set>/<scenario_id>/<episode_id>.jsonl`
- Manifest: `SupervisorAgent/runtime/episodes/<episode_set>/episodes_manifest.json`
- Run folders: `SupervisorAgent/runtime/runs/<run_id>/` (with episode tags)
- Report: `SupervisorAgent/runtime/reports/<episode_set>/report.json` and `report.md`
