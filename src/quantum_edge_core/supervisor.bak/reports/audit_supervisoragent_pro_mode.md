# SupervisorAgent Audit (Pro Mode)

## 0) Executive Summary
Partial – SupervisorAgent wires basic process control, heartbeat intake, and risk checks, but defaults leave the API unauthenticated, TSDB/snapshot/dashboards are best-effort only, and no exposure-aware policy engine or operational hardening is in place.
- API listener has auth disabled by default and CORS wildcard; single-threaded HTTPServer (`supervisor/api_server.py:ApiServer.start` + `supervisor/config.py:load_supervisor_config` default `api_auth_token=''`).
- RiskEngine enforces only per-order notional/leverage and equity/drawdown; no position/exposure awareness (`supervisor/risk_engine.py:evaluate_order` TODO comment on exposure).
- Heartbeat freshness does not influence supervision; `run_foreground` only restarts on process death, ignoring stale heartbeats (`supervisor.py:SupervisorApp.run_foreground`).
- TSDB pipeline is disabled by default (`config/tsdb.yaml backend: none`); writer/query is optional write-only (`supervisor.py:_build_tsdb_writer`, `supervisor/tsdb/writer.py`).
- Snapshots skip when no recent events, so dashboard/health can be empty (`supervisor/tasks/snapshot_scheduler.py:run_once`).
- Dashboard endpoints are unauthenticated and return partial data (pnl hardcoded 0, open_orders 0) (`supervisor/dashboard/service.py:get_overview`, exposed via `api_server.py`).
- LLM/trend/market monitors are disabled by config and block on external API with no backoff beyond simple timeout (`supervisor/llm_supervisor.py:run_check`, `supervisor/llm/chat_client.py:complete`).
- Event and snapshot logs grow unbounded and are not gitignored (`supervisor/events.py:EventLogger.log_event` writes daily JSONL; no `.gitignore`), risking disk bloat/accidental commits.
- No automated tests or CI coverage; only runtime code present (`requirements.txt` minimal, no tests directory).
- TSDB maintenance/backfill exists only as manual CLI commands (`supervisor.py` commands `tsdb-backfill`/`tsdb-maintain`), not scheduled.

## 1) Repo Map (module inventory)
| Module/Area | Paths | Status | Evidence | Notes |
| --- | --- | --- | --- | --- |
| Entrypoints & service startup | `supervisor.py:main`, `supervisor.py:build_app`, `SupervisorApp.run_foreground` | ✅ confirmed | CLI dispatch + app wiring in `supervisor.py` | Runs as CLI; foreground loop handles restarts and scheduled tasks. |
| API layer | `supervisor/api_server.py` | ✅ confirmed | Handler with `/api/v1/*` routes in `ApiServer.start` | Single-threaded HTTPServer, optional X-API-TOKEN. |
| PolicyEngine (operational director) | `supervisor.py:run_foreground`, `supervisor/risk_engine.py:_evaluate_auto_halt` | 🟡 partial | Loop restarts bot; risk auto-halt only | No policy on stale heartbeat/mode switching; no position-aware checks. |
| Risk evaluation service | `supervisor/risk_engine.py:evaluate_order` | ✅ confirmed | Order gating on notional/leverage + halt flags | No exposure/trade-history inputs. |
| Signal review / LLM supervisor | `supervisor/llm_supervisor.py`, `supervisor/llm/chat_client.py` | 🟡 partial | `LlmSupervisor.run_check` calls external API if enabled | Disabled by default; blocking HTTP; trust policy optional. |
| Snapshots & strategy supervisor | `supervisor/tasks/snapshot_scheduler.py`, `supervisor/meta_supervisor.py` | 🟡 partial | `SnapshotScheduler.run_once`, `MetaSupervisorRunner.run_cycle` | Snapshots require recent events; meta-supervisor manual/interval-gated. |
| TSDB writer/query layer | `supervisor/tsdb/*`, `supervisor.py:_build_tsdb_writer` | 🟡 partial | ClickHouse/QuestDB stores + `TsdbWriter` | Default backend none; write-only; no query API. |
| Health/diag | `supervisor/api_server.py` status route, `supervisor.py:run_diag`, `supervisor/heartbeat.py` | ✅ confirmed | Health snapshot via API; diag CLI prints config checks | No liveness endpoint beyond status. |
| Dashboard backend endpoints | `supervisor/dashboard/service.py`, `api_server.py` routes | 🟡 partial | `dashboard_overview/health/events` handlers | No auth; PnL placeholder; depends on JSONL logs. |
| Process manager / QuantumEdge integration | `supervisor/process_manager.py` | ✅ confirmed | `_spawn_process` runs `C:/ai_scalper_bot/run_bot.py` with mode | Tracks PID/exit; restart/backoff; no health probes. |
| Events schema & logging pipeline | `supervisor/events.py` | ✅ confirmed | `EventLogger.log_event` JSONL schema | Optional TSDB enqueue; no rotation. |
| Config pack | `config/*.yaml`, `supervisor/config.py` | ✅ confirmed | Loaders for paths/supervisor/risk/llm/meta/dashboard/tsdb | Defaults provided when files missing. |
| Tests | (none) | ❌ missing | No test files or CI config | Coverage absent. |
| Ops deploy templates | (none) | ❌ missing | No systemd/NSSM/docker scripts | Manual invocation only. |

## 2) Runtime wiring (trace)
- `supervisor.py:main` parses CLI -> `build_app` loads YAML configs (paths, supervisor, risk, llm, meta, dashboard, tsdb) and sets logging to `logs/supervisor.log`.
- `SupervisorApp.__init__` builds `EventLogger` writing to `logs/events/events_YYYY-MM-DD.jsonl` (and `logs/snapshots`), optional `TsdbWriter`, `HeartbeatServer`, `RiskEngine` seeded from `state/risk_state.json`, `ProcessManager` targeting `config/paths.yaml:quantumedge_root/run_bot.py`, LLM/trend/market/behavior analyzers, `SnapshotScheduler`, `DashboardService`, and `ApiServer` (if enabled on `heartbeat_port`).
- CLI commands (`start/stop/restart/status/risk-status/audit/llm-check/meta-supervisor/snapshot/diag/tsdb-*`) drive `SupervisorApp` methods; `run_foreground` starts API server thread then loops (2s) to restart bot if dead, schedule LLM checks, and snapshot generation.
- Heartbeat intake: `POST /api/v1/heartbeat` -> `SupervisorApp.handle_heartbeat` updates `HeartbeatServer` + `RiskEngine`, persists state under `state/`.
- Order gating: `POST /api/v1/risk/evaluate` -> `SupervisorApp.evaluate_order_from_json` -> `RiskEngine.evaluate_order` -> event logged (and TSDB if enabled).
- Dashboard/health: `GET /api/v1/status` returns bot/heartbeat/risk snapshot; dashboard routes read JSONL events + heartbeat and latest snapshot from `SnapshotScheduler`; `GET /api/v1/tsdb/status` inspects writer state.
- Snapshot scheduler: `SnapshotScheduler.run_once` pulls recent events, runs LLM-based analyzers, emits `SUPERVISOR_SNAPSHOT` events and persists latest snapshot.
- Meta supervisor: `SupervisorApp.run_meta_supervisor_once` invokes `MetaSupervisorRunner.run_cycle`, optionally running Meta-Agent via subprocess, logging `META_SUPERVISOR_*` events and persisting state.
- TSDB: if enabled, `EventLogger` enqueues points mapped in `supervisor/tsdb/mappers.py` to `TsdbWriter` background thread; schema scripts in `sql/`.

## 3) API surface audit
- `POST /api/v1/heartbeat` → `SupervisorApp.handle_heartbeat` (`api_server.py` Handler): body any heartbeat fields (uptime_s, equity, realized_pnl_today, trading_day, etc.), response includes heartbeat_status, last_heartbeat_time, risk flags (halted/llm_paused/multiplier). Auth via `X-API-TOKEN` if configured; CORS `*`.
- `POST /api/v1/risk/evaluate` → `SupervisorApp.evaluate_order_from_json`: expects symbol, side, order_type, quantity with optional price/notional/leverage/is_reduce_only; returns allowed/code/reason + risk flags. Validation errors return 400.
- `GET /api/v1/status` → `SupervisorApp.get_status_snapshot`: bot PID/uptime or last exit, heartbeat status/time, risk flags.
- `GET /api/v1/supervisor/snapshot` → `SupervisorApp.get_latest_snapshot_payload`: latest snapshot dict or defaults.
- `GET /api/v1/dashboard/overview` → `SupervisorApp.dashboard_overview`: timestamp, total_pnl(0.0), pnl_1h, open_positions (from heartbeat), strategy_mode, trend/risk level.
- `GET /api/v1/dashboard/health` → `SupervisorApp.dashboard_health`: status, issues list, last heartbeat/snapshot timestamps.
- `GET /api/v1/dashboard/events[?limit=&types=]` → `SupervisorApp.dashboard_events`: recent events mapped to timestamp/type/symbol/details.
- `GET /api/v1/tsdb/status` → `SupervisorApp.get_tsdb_status`: enabled/backend/reachable/last_write/queue_depth.
- Control endpoints (mode switch, pause/resume, signal review) are not present.

## 4) PolicyEngine audit
- Evaluation cadence: foreground loop every ~2s (`SupervisorApp.run_foreground`) with LLM checks on `llm_config.check_interval_minutes` and snapshots on `snapshots.interval_minutes`.
- Inputs: risk state from heartbeats (`RiskEngine.update_from_heartbeat`), per-order requests (`evaluate_order`), optional LLM advice (`llm_supervisor.run_check`), snapshot analyzers reading recent events.
- Outputs/actions: auto-halt and llm_pause/risk_multiplier flags persisted to `state/risk_state.json`; order allow/block codes; process start/stop/restart invoked only via CLI or death detection; no automatic mode switch/pause API.
- Persistence/audit: events logged via `EventLogger`; risk state persisted on heartbeat/order evaluations.
- Kill switch/manual override: auto-halt on limit breach and llm soft pause; no manual API to toggle halt/pause; stale heartbeat not auto-halts.

## 5) TSDB integration audit
- Backends supported: ClickHouse (`supervisor/tsdb/clickhouse.py`) and QuestDB (`supervisor/tsdb/questdb.py`); retry/backoff in ClickHouse writer; QuestDB best-effort.
- Writer: `TsdbWriter` background thread buffers and flushes (`supervisor/tsdb/writer.py`); started in `SupervisorApp._build_tsdb_writer` only if `config/tsdb.yaml` enables non-`none` backend.
- Schema: JSON fields/tags written to `qe_tsdb_points` (prefix configurable); sample DDL in `sql/clickhouse_schema.sql` and `sql/questdb_schema.sql`. No query layer/APIs.
- Error handling: writes logged as warnings; enqueue from events best-effort; no blocking in request handlers besides minimal mapping.
- TSDB optional/degraded mode: default backend `none`; system runs off JSONL when TSDB disabled. Retention/rollups only via manual `tsdb-maintain` CLI (`supervisor/tsdb/maintenance.py`).

## 6) Snapshots / events / audit trail
- Events: JSONL per day under `logs/events/events_YYYY-MM-DD.jsonl` with types from `EventType` (`supervisor/events.py`); includes order decisions/results, bot starts/stops, risk breaches, LLM/meta events, strategy updates; no IDs/correlation beyond timestamp/symbol.
- Snapshots: `SnapshotScheduler.run_once` writes `SUPERVISOR_SNAPSHOT` events and appends to `logs/snapshots/snapshots_DATE.jsonl`; latest cached at `state/last_snapshot.json` (`supervisor/tasks/snapshot_scheduler.py`). Frequency from `config/supervisor.yaml` `snapshots.*` but only executed in foreground loop or manual `snapshot` command.
- Audit: `supervisor.py audit` loads daily events via `audit_report.py` to generate `reports/audit_YYYY-MM-DD.md`; stats limited to counts/winrate; report renderer has minor formatting issues but outputs markdown.
- Retention/rotation: none for events/snapshots; growth unbounded.

## 7) Health / diag / observability
- Heartbeat tracking: `HeartbeatServer` holds last heartbeat + status (`supervisor/heartbeat.py`); status reported via API/status and dashboard health; no auto-restart on stale.
- Logging: supervisor log rotates (`supervisor/logging_setup.py` RotatingFileHandler 5x5MB); child bot logs created per start with timestamp (no rotation) in `logs/quantumedge_*.log` (`process_manager.py`).
- Diag: `python supervisor.py diag` runs checks on paths, configs, snapshot/dashboard/TSDB flags (`SupervisorApp.run_diag`).
- Metrics: none beyond JSON/TSDB writes; no Prometheus/liveness endpoints.

## 8) Security audit (repo hygiene)
- API/CORS: auth token blank by default (`config/supervisor.yaml`), CORS `*`, HTTP only via `http.server`; risk of local abuse if port exposed.
- Secrets: no secrets committed; TSDB credentials stored in plain YAML if configured (`config/tsdb.yaml`).
- Files to gitignore: `logs/`, `logs/events/`, `logs/snapshots/`, `logs/quantumedge_*.log`, `state/`, `reports/`, `output/` to avoid leaking runtime data.
- Install scripts: none; python_executable path is trusted from config; no execution-policy bypass present.
- Deserialization: JSON parsing via stdlib; no eval usage.

## 9) Pro mode readiness checklist
- Snapshots & strategy supervisor: 🟡 partial – snapshots rely on recent events and run only in foreground/manual; Meta supervisor requires configured Meta-Agent; need daemonized scheduler and data availability to reach ✅.
- Dashboard backend: 🟡 partial – endpoints exist but unauthenticated and metrics are placeholders; need auth, real PnL/open_orders sources, and TSDB-backed queries for ✅.
- TSDB writer/query: 🟡 partial – write-only optional backend; default disabled; add enforced backend, health checks, and read/query surface to reach ✅.
- Health / diag: ✅ – status endpoint and `diag` command provide basic checks; could expand with liveness/staleness actions.
- Policy actions (pause/lower-risk/run-meta): 🟡 partial – auto-halt + LLM pause exist; no manual API or automated mode switch/cooldowns; add control endpoints and exposure-aware policies for ✅.
- Integration with QuantumEdge (mode control + status ingest): 🟡 partial – process manager launches `run_bot.py` and ingest heartbeats/orders, but no contract for position/exposure or supervisor-driven mode changes; require explicit protocol and enforcement to reach ✅.

## 10) Concrete next actions (non-code)
- Enforce API auth + CORS/TLS policy; document header requirements (`supervisor/api_server.py`, `config/supervisor.yaml`) – Complexity S – reduces unauthorized access.
- Tie heartbeat staleness to bot restart/alerts; define thresholds and operator actions (`supervisor.py:run_foreground`, `supervisor/heartbeat.py`) – Complexity M – mitigates silent hangs.
- Extend risk model with exposure/position inputs from QuantumEdge and persist symbol exposure (`supervisor/risk_engine.py`) – Complexity M – closes per-symbol risk gaps.
- Schedule snapshot generation outside foreground loop (service/cron) and guarantee minimal event feed; define fallback behavior when events missing (`supervisor/tasks/snapshot_scheduler.py`) – Complexity M – ensures dashboards/LLM have data.
- Decide TSDB strategy: enable ClickHouse/QuestDB, apply schema/retention automatically, and add basic query endpoints or exports (`supervisor/tsdb/*`, `sql/*.sql`) – Complexity M – improves observability durability.
- Protect dashboard routes with the same auth token and add meaningful PnL/open_orders sources (heartbeat/TSDB) (`supervisor/dashboard/service.py`, `api_server.py`) – Complexity M – prevents data spoofing and improves usefulness.
- Add control/override endpoints for pause/resume/mode switch with auditing (`supervisor/api_server.py`, `supervisor/events.py`) – Complexity M – enables operator interventions.
- Implement log/data retention policy and gitignore runtime artifacts (`logs/`, `state/`, `reports/`) – Complexity S – prevents disk bloat and data leaks.
- Add automated tests for risk decisions and API handlers; wire into CI (new `tests/` hitting `risk_engine.evaluate_order` and `ApiServer` handler) – Complexity M – raises confidence.
- Document deployment recipes (systemd/NSSM, sample service configs) and operational runbooks (tsdb-backfill/maintain) in `Documentation/` – Complexity S – eases production rollout.
- Define LLM timeout/backoff and resiliency plan; consider async client or circuit-breaker when LLM is enabled (`supervisor/llm/chat_client.py`, `llm_supervisor.py`) – Complexity M – avoids blocking supervision.
- Publish contract for heartbeat/order schema (positions/notional fields required) and validate payloads strictly (`supervisor/heartbeat.py`, `api_server.py`) – Complexity S – prevents malformed data.
