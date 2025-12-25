# QuantumEdge Configuration

This monorepo uses a shared configuration contract rooted at `QE_ROOT` (defaults to the repo root).

## Core files (root `config/`)
- `config/quantumedge.yaml`: global defaults (Supervisor host/port, orchestrator settings)
- `config/paths.yaml`: path aliases relative to `QE_ROOT`
- `config/supervisor.yaml`: Supervisor runtime defaults + bot wiring
- `config/bot.yaml`: bot runtime defaults (safe, no secrets)
- `config/meta_agent.yaml`: Meta-Agent defaults and registry paths
- `config/env.example`: environment variable list (no values)

## Module config directories
The SupervisorAgent, bot, and Meta-Agent still keep their detailed module configs under:
- `SupervisorAgent/config/`
- `ai_scalper_bot/config/`
- `meta_agent/config/`

`config/paths.yaml` points to these directories so each module can resolve its own config set.

## Environment variables (highlights)
- `QE_ROOT`: repo root (defaults to autodetected)
- `QE_CONFIG_DIR`, `QE_RUNTIME_DIR`, `QE_LOGS_DIR`, `QE_DATA_DIR`: shared paths
- `SUPERVISOR_HOST`, `SUPERVISOR_PORT`, `SUPERVISOR_URL`: supervisor endpoint overrides
- `SUPERVISOR_CONFIG`, `BOT_CONFIG`, `META_AGENT_CONFIG`: per-module config overrides

See `config/env.example` for the full list.

## Orchestrator (recommended)
Use the single entrypoint from the repo root:
- Start: `python QuantumEdge.py start --with-meta`
- Status: `python QuantumEdge.py status`
- Stop: `python QuantumEdge.py stop`
- Diag: `python QuantumEdge.py diag`

`config/quantumedge.yaml` includes orchestrator defaults (health probe path, startup timeout, and whether Supervisor spawns the bot).
The Supervisor health endpoint defaults to `/api/v1/dashboard/health` and can be overridden via `config/supervisor.yaml` (`health_path`).

## Bot lifecycle (Supervisor-managed)
Supervisor is the single authority for bot lifecycle. Configuration lives in `config/supervisor.yaml`:
- `bot.auto_start`: start the bot automatically on Supervisor boot.
- `bot.restart.enabled`: restart on crash with bounded retries.
- `bot.restart.max_retries` and `bot.restart.backoff_seconds`: restart policy.

Supervisor API endpoints (local, no secrets):
- `GET /api/v1/bot/status`: current bot state (RUNNING/STOPPED/CRASHED/FAILED/STARTING).
- `POST /api/v1/bot/start`: start the bot if stopped.
- `POST /api/v1/bot/stop`: stop the bot.
- `POST /api/v1/bot/restart`: restart the bot.

All commands assume a single root `.venv` and use `QE_ROOT` for resolution.
