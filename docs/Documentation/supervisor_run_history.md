# Supervisor Run History (Stage 1)

Each Supervisor run creates a durable run folder:

```
src/quantum_edge_core/supervisor/runtime/runs/<run_id>/
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
   - `python src/quantum_edge_core/supervisor/supervisor.py run-foreground --episode-set tick_scenarios_v1 --scenario-id S01`
2) Verify a new folder in:
   - `src/quantum_edge_core/supervisor/runtime/runs/<run_id>/`
3) Confirm:
   - `events.jsonl` has RUN_START and RUN_END
   - `summary.json` exists
   - `config_snapshot.json` exists with secrets redacted
    - `artifacts.json` exists and lists files
4) Check log rotation:
   - `src/quantum_edge_core/supervisor/runtime/logs/supervisor.log` (+ rotated files)

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
- `src/quantum_edge_core/supervisor/episodes/scenarios_v1.yaml`

Commands:
1) Cut episodes:
   - `python src/quantum_edge_core/supervisor/supervisor.py episodes-cut --episode-set smoke_v1 --ticks-path data/ticks --format jsonl`
   - If no tick file exists yet: add `--synthetic` to generate a tiny JSONL sample.
2) Run episodes:
   - `python src/quantum_edge_core/supervisor/supervisor.py episodes-run --episode-set smoke_v1 --episodes-manifest src/quantum_edge_core/supervisor/runtime/episodes/smoke_v1/episodes_manifest.json`
3) Report:
   - `python src/quantum_edge_core/supervisor/supervisor.py episodes-report --episode-set smoke_v1`

Artifacts:
- Episodes: `src/quantum_edge_core/supervisor/runtime/episodes/<episode_set>/<scenario_id>/<episode_id>.jsonl`
- Manifest: `src/quantum_edge_core/supervisor/runtime/episodes/<episode_set>/episodes_manifest.json`
- Run folders: `src/quantum_edge_core/supervisor/runtime/runs/<run_id>/` (with episode tags)
- Report: `src/quantum_edge_core/supervisor/runtime/reports/<episode_set>/report.json` and `report.md`

## Ops Brain v1 (auto-tuning + regression gates)
Ops automation produces policy versions and gate reports under runtime:

```
src/quantum_edge_core/supervisor/runtime/policy_versions/
  policy_vNNN.yaml
  policy_vNNN_manifest.json
  active_policy.yaml
  active_policy_manifest.json

src/quantum_edge_core/supervisor/runtime/regression/<policy_version>/
  gate_report.json
```

Commands:
1) Autotune (dry-run default):
   - `python src/quantum_edge_core/supervisor/supervisor.py ops-autotune --episode-set tick_scenarios_v1`
   - Add `--apply` to activate if gates pass.
2) Regression gate on a version:
   - `python src/quantum_edge_core/supervisor/supervisor.py ops-regression-gate --policy-version v001 --episode-set tick_scenarios_v1`
3) Daily report:
   - `python src/quantum_edge_core/supervisor/supervisor.py ops-daily-report --date YYYY-MM-DD`
4) Rollback:
   - `python src/quantum_edge_core/supervisor/supervisor.py ops-rollback --policy-version v001`
