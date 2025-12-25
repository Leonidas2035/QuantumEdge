# SupervisorAgent

SupervisorAgent is a lightweight controller process for the QuantumEdge trading bot. It launches, monitors, and stops the trading engine from outside the hot trading loop, paving the way for risk controls, LLM oversight, and Meta-Agent strategic cycles.

## Requirements
- Python 3.9+ (uses only standard library + PyYAML)
- Install dependency: `pip install pyyaml`

## Configuration
Edit the YAML files under `config/`:
- `config/paths.yaml` sets paths to QuantumEdge (`quantumedge_root`), Python executable (leave empty to auto-detect), Meta-Agent root, and the supervisor logs directory.
- `config/supervisor.yaml` sets runtime options: mode (`paper`/`demo`/`off`), heartbeat settings, restart policy, and HTTP API settings.
- `config/risk.yaml` sets global risk limits (currency, daily loss, drawdown, per-symbol notional, leverage).
- `config/llm_supervisor.yaml` configures the LLM watchdog.
- `config/meta_supervisor.yaml` configures Meta-Agent orchestration.

## Commands
Run from the repo root (`QE_ROOT`):
- `python supervisor.py start` ??? start QuantumEdge in the configured mode.
- `python supervisor.py status` ??? show process and heartbeat status.
- `python supervisor.py stop` ??? stop the running QuantumEdge process.
- `python supervisor.py restart` ??? restart QuantumEdge with backoff.
- `python supervisor.py risk-status` ??? show detailed risk engine state.
- `python supervisor.py run-foreground` ??? start and keep supervising with auto-restart attempts (starts HTTP API if enabled).
- `python supervisor.py audit` ??? generate an audit summary for today.
- `python supervisor.py audit --date YYYY-MM-DD` ??? generate an audit summary for a specific date.
- `python supervisor.py llm-check` ??? run an on-demand LLM moderation check.
- `python supervisor.py meta-supervisor` ??? trigger a Meta-Agent strategic supervisor cycle (if allowed by config).
- `python supervisor.py meta-supervisor --force` ??? force a Meta-Agent supervisor run, ignoring spacing/idle checks.

## Stage 2 ??? Global Risk Engine
- Configure global limits in `config/risk.yaml` (currency, daily loss, drawdown, per-symbol notional, leverage).
- When limits are breached, the supervisor enters AUTO-HALT: only reduce-only orders are allowed; all other orders are denied with reason.
- QuantumEdge will route order requests through the supervisor risk API (`OrderRequest -> RiskDecision`).

## Stage 3 ??? Logging & Audits
- Structured JSONL event logs are written under `logs/events/` (one file per day).
- Events capture order decisions, risk limit breaches, bot lifecycle actions, and anomalies.
- Use `python supervisor.py audit [--date YYYY-MM-DD]` to compute a daily summary; a Markdown report is written to `reports/audit_YYYY-MM-DD.md`.

## Stage 4 ??? LLM Supervisor (AI watchdog)
- `config/llm_supervisor.yaml` configures the AI duty officer: enable flag, model, API URL, API key env var, check interval, dry-run, and trust policy.
- Possible actions from the LLM: `OK` (continue), `LOWER_RISK` (tighten via `llm_risk_multiplier`), `PAUSE` (soft halt: only risk-reducing orders), `SWITCH_TO_PAPER` (currently advisory unless trust_policy allows mode switch).
- Hard risk limits (AUTO-HALT) always override any LLM advice. Advice is logged as `LLM_ADVICE` events and reflected in risk state.

## Stage 5 ??? Strategic AI via Meta-Agent
- `config/meta_supervisor.yaml` controls orchestration of Meta-Agent supervisor cycles (off-market audits/strategy work). Key flags: `enabled`, `meta_agent_root`, `python_executable`, `project_id`, `frequency_days`, `min_hours_between_runs`, `require_bot_idle`, `dry_run`, `use_supervisor_runner`.
- Command: `python supervisor.py meta-supervisor [--force]` runs a cycle (or skips if not allowed). Results and skips are logged as `META_SUPERVISOR_*` events; state lives at `state/meta_supervisor_state.json`.
- Meta-Agent reports are read from `<meta_agent_root>/reports/supervisor/` after runs.

## Stage 6 ??? HTTP API (Heartbeat + Risk Gateway)
- Supervisor exposes a JSON API (defaults: host `127.0.0.1`, port = `heartbeat_port`) controlled by `api_enabled`, `api_host`, `api_auth_token` in `config/supervisor.yaml`. When `api_auth_token` is set, clients must send header `X-API-TOKEN`.
- Endpoints:
  - `POST /api/v1/heartbeat` ??? body: heartbeat JSON; updates supervisor heartbeat/risk state; returns heartbeat status and risk flags.
  - `POST /api/v1/risk/evaluate` ??? body: order request JSON (symbol, side, order_type, quantity, price/notional, leverage, is_reduce_only); returns risk decision and risk flags.
  - `GET /api/v1/status` ??? returns overall snapshot: bot running status, heartbeat state, and risk flags.
- Intended callers: QuantumEdge runtime and internal monitoring tools. Hard risk limits and trust policies still apply; this API does not bypass them.
# SupervisorAgent

SupervisorAgent is a lightweight controller process for the QuantumEdge trading bot. It launches, monitors, and stops the trading engine from outside the hot trading loop, paving the way for risk controls, LLM oversight, and Meta-Agent strategic cycles.

## Requirements
- Python 3.9+ (uses only standard library + PyYAML)
- Install dependency: `pip install pyyaml`

## Configuration
Edit the YAML files under `config/`:
- `config/paths.yaml` sets paths to QuantumEdge (`quantumedge_root`), Python executable (leave empty to auto-detect), Meta-Agent root, and the supervisor logs directory.
- `config/supervisor.yaml` sets runtime options: mode (`paper`/`demo`/`off`), heartbeat settings, restart policy, and HTTP API settings.
- `config/risk.yaml` sets global risk limits (currency, daily loss, drawdown, per-symbol notional, leverage).
- `config/llm_supervisor.yaml` configures the LLM watchdog.
- `config/meta_supervisor.yaml` configures Meta-Agent orchestration.
- `config/dashboard.yaml` controls the dashboard API (overview/health/events).
- `config/tsdb.yaml` controls optional timeseries persistence (ClickHouse-ready); set `backend: none` or `enabled: false` to disable.

## Commands
Run from the repo root (`QE_ROOT`):
- `python supervisor.py start` ??? start QuantumEdge in the configured mode.
- `python supervisor.py status` ??? show process and heartbeat status.
- `python supervisor.py stop` ??? stop the running QuantumEdge process.
- `python supervisor.py restart` ??? restart QuantumEdge with backoff.
- `python supervisor.py risk-status` ??? show detailed risk engine state.
- `python supervisor.py run-foreground` ??? start and keep supervising with auto-restart attempts (starts HTTP API if enabled).
- `python supervisor.py audit` ??? generate an audit summary for today.
- `python supervisor.py audit --date YYYY-MM-DD` ??? generate an audit summary for a specific date.
- `python supervisor.py llm-check` ??? run an on-demand LLM moderation check.
- `python supervisor.py meta-supervisor` ??? trigger a Meta-Agent strategic supervisor cycle (if allowed by config).
- `python supervisor.py meta-supervisor --force` ??? force a Meta-Agent supervisor run, ignoring spacing/idle checks.
- `python supervisor.py snapshot` ??? generate a Supervisor snapshot immediately.
- `python supervisor.py diag` ??? run diagnostics (config, heartbeat, snapshot, dashboard/TSDB readiness).

## Stage 2 ??? Global Risk Engine
- Configure global limits in `config/risk.yaml` (currency, daily loss, drawdown, per-symbol notional, leverage).
- When limits are breached, the supervisor enters AUTO-HALT: only reduce-only orders are allowed; all other orders are denied with reason.
- QuantumEdge will route order requests through the supervisor risk API (`OrderRequest -> RiskDecision`).

## Stage 3 ??? Logging & Audits
- Structured JSONL event logs are written under `logs/events/` (one file per day).
- Events capture order decisions, risk limit breaches, bot lifecycle actions, and anomalies.
- Use `python supervisor.py audit [--date YYYY-MM-DD]` to compute a daily summary; a Markdown report is written to `reports/audit_YYYY-MM-DD.md`.

## Stage 4 ??? LLM Supervisor (AI watchdog)
- `config/llm_supervisor.yaml` configures the AI duty officer: enable flag, model, API URL, API key env var, check interval, dry-run, and trust policy.
- Possible actions from the LLM: `OK` (continue), `LOWER_RISK` (tighten via `llm_risk_multiplier`), `PAUSE` (soft halt: only risk-reducing orders), `SWITCH_TO_PAPER` (currently advisory unless trust_policy allows mode switch).
- Hard risk limits (AUTO-HALT) always override any LLM advice. Advice is logged as `LLM_ADVICE` events and reflected in risk state.

## Stage 5 ??? Strategic AI via Meta-Agent
- `config/meta_supervisor.yaml` controls orchestration of Meta-Agent supervisor cycles (off-market audits/strategy work). Key flags: `enabled`, `meta_agent_root`, `python_executable`, `project_id`, `frequency_days`, `min_hours_between_runs`, `require_bot_idle`, `dry_run`, `use_supervisor_runner`.
- Command: `python supervisor.py meta-supervisor [--force]` runs a cycle (or skips if not allowed). Results and skips are logged as `META_SUPERVISOR_*` events; state lives at `state/meta_supervisor_state.json`.
- Meta-Agent reports are read from `<meta_agent_root>/reports/supervisor/` after runs.

## Stage 6 ??? HTTP API (Heartbeat + Risk Gateway)
- Supervisor exposes a JSON API (defaults: host `127.0.0.1`, port = `heartbeat_port`) controlled by `api_enabled`, `api_host`, `api_auth_token` in `config/supervisor.yaml`. When `api_auth_token` is set, clients must send header `X-API-TOKEN`.
- Endpoints:
  - `POST /api/v1/heartbeat` ??? body: heartbeat JSON; updates supervisor heartbeat/risk state; returns heartbeat status and risk flags.
  - `POST /api/v1/risk/evaluate` ??? body: order request JSON (symbol, side, order_type, quantity, price/notional, leverage, is_reduce_only); returns risk decision and risk flags.
  - `GET /api/v1/status` ??? returns overall snapshot: bot running status, heartbeat state, and risk flags.
- Intended callers: QuantumEdge runtime and internal monitoring tools. Hard risk limits and trust policies still apply; this API does not bypass them.

## Stage 9 ??? Dashboard & TSDB (observability)
- JSON dashboard endpoints:
  - `GET /api/v1/dashboard/overview`
  - `GET /api/v1/dashboard/health`
  - `GET /api/v1/dashboard/events`
- Optional TSDB pipeline (config/tsdb.yaml) can persist snapshots/orders/trades for charting. A sample ClickHouse schema is provided in `sql/clickhouse_schema.sql`. If TSDB is disabled, the system continues to operate using JSONL events only.
