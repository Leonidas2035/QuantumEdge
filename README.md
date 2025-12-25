# QuantumEdge Monorepo

This repository combines multiple projects under a single root for coordinated development.

Projects:
- ai_scalper_bot/
- SupervisorAgent/
- meta_agent/

Shared (kept empty or runtime-only):
- config/
- runtime/
- logs/
- data/

Config:
- See `docs/CONFIG.md` for unified QE_ROOT-based settings and run commands.

Rule: do not commit secrets, API keys, or encrypted secret files. Keep those local and out of git.

Recommended entrypoint:
- `python QuantumEdge.py start --with-meta`
- `python QuantumEdge.py status`
- `python QuantumEdge.py stop`

Supervisor health endpoint: `/api/v1/dashboard/health` (configurable via `config/supervisor.yaml` `health_path`).

Policy contract (Supervisor -> bot):
- File: `runtime/policy.json`
- API: `GET /api/v1/policy/current`
- Schema: `docs/policy_schema_v1.json`
- Policy engine config lives in `config/supervisor.yaml` (heuristics + optional LLM moderation).

Telemetry (bot -> SupervisorAgent):
- Ingest: `POST /api/v1/telemetry/ingest`
- Summary: `GET /api/v1/telemetry/summary`
- Events: `GET /api/v1/telemetry/events?limit=200`
- Alerts: `GET /api/v1/telemetry/alerts`

Ops entrypoints:
- `python QuantumEdge.py start|stop|restart|status|diag`
- `python SupervisorAgent/supervisor.py start|stop|restart|status|diag`

Cross-platform scripts:
- `scripts/windows/qe_start.ps1`, `scripts/windows/qe_stop.ps1`, `scripts/windows/qe_diag.ps1`
- `scripts/linux/qe_start.sh`, `scripts/linux/qe_stop.sh`, `scripts/linux/qe_diag.sh`

Runtime dependencies:
- Bot-only runtime: `requirements/requirements-runtime.txt`
- Full stack (Supervisor + research + meta-agent): `requirements/requirements.txt`

Runtime vs research:
- Live trading runtime remains under `ai_scalper_bot/bot`.
- Offline/backtest tooling moved to `SupervisorAgent/research/` (compat wrappers remain under `ai_scalper_bot` for one stage).

ModelOps (SupervisorAgent):
- Train/publish models: `python SupervisorAgent/supervisor.py ml train --symbol BTCUSDT --horizons 1,5,30 --source ticks --input-dir data/ticks --publish`
- Runtime models live under `runtime/models/<symbol>/<horizon>/current/`

Research suite (SupervisorAgent):
- Backtest: `python SupervisorAgent/supervisor.py research backtest --symbol BTCUSDT --data_dir data/ticks`
- Replay: `python SupervisorAgent/supervisor.py research replay --symbol BTCUSDT --data_dir data/ticks --speed 1.0`
- Scenario: `python SupervisorAgent/supervisor.py research scenario --name spread_spike --symbol BTCUSDT --data_dir data/ticks`
