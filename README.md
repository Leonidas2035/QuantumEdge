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

Runtime vs research:
- Live trading runtime remains under `ai_scalper_bot/bot`.
- Offline/backtest tooling moved to `SupervisorAgent/research/` (compat wrappers remain under `ai_scalper_bot` for one stage).
