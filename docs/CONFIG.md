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

## ModelOps (SupervisorAgent)
Supervisor owns model training and publishing under `SupervisorAgent/mlops/`:
- Artifacts: `artifacts/models/<symbol>/<horizon>/<version>/`
- Published runtime: `runtime/models/<symbol>/<horizon>/current/`
- Manifest: `runtime/models/.../manifest.json` (version `model.v1`)

Example commands:
- Dataset: `python SupervisorAgent/supervisor.py ml dataset --symbol BTCUSDT --source ticks --input-dir data/ticks`
- Train: `python SupervisorAgent/supervisor.py ml train --symbol BTCUSDT --horizons 1,5,30 --source ticks --input-dir data/ticks --publish`
- Validate: `python SupervisorAgent/supervisor.py ml validate --manifest artifacts/models/BTCUSDT/1/<version>/manifest.json --dataset artifacts/datasets/BTCUSDT/ticks/<version>/BTCUSDT_h1.csv`
- Publish: `python SupervisorAgent/supervisor.py ml publish --artifact-dir artifacts/models/BTCUSDT/1/<version>`

Bot loading (from `config/bot.yaml`):
- `ml.model_source`: `runtime` (default) or `legacy`
- `ml.runtime_models_dir`: default `runtime/models`
- `ml.ml_required`: if true, missing/invalid models disable trading

## Policy contract (Supervisor -> bot)
Supervisor publishes a versioned policy contract that the bot consumes:
- File: `runtime/policy.json` (atomic write every few seconds)
- API: `GET /api/v1/policy/current`
- Optional debug: `GET /api/v1/policy/debug` (signals + heuristics + LLM/circuit breaker state)
- Schema: `docs/policy_schema_v1.json`

Bot defaults (from `config/bot.yaml`):
- `policy.policy_source`: `file` | `api` | `auto` (file-first)
- `policy.policy_file_path`: default `runtime/policy.json`
- `policy.policy_api_url`: default `http://127.0.0.1:8765/api/v1/policy/current`
- `policy.policy_ttl_grace_sec`: default `0`
- `policy.safe_mode_default`: default `risk_off`

If no fresh policy is available, the bot enters safe mode (no new entries, exits allowed).

### Policy engine (SupervisorAgent)
Policy is computed on a schedule using deterministic heuristics and optional LLM moderation.
Key settings live in `config/supervisor.yaml`:
- `policy.update_interval_sec`: how often to recompute/publish policy.
- `policy.ttl_sec`: policy freshness window.
- `policy.hysteresis.enter_cycles` / `policy.hysteresis.exit_cycles`: anti-flap controls.
- `policy.thresholds.*`: loss limits, restart-rate thresholds, spread/volatility gating.
- `llm.enabled`: enable optional LLM moderation (default false).
- `llm.api_key_env`: env var for the LLM key (local only).
- `llm.timeout_sec`: LLM time budget (seconds).
- `llm.circuit_breaker.failures`, `llm.circuit_breaker.window_sec`, `llm.circuit_breaker.open_sec`.

If LLM is enabled but unavailable, policy falls back to heuristics with a reason suffix (`LLM_UNAVAILABLE` or `LLM_CB_OPEN`).

## Secrets (local only, BingX demo)
Secrets are loaded locally and never committed.

Recommended workflow:
1) Copy `config/secrets.local.env.example` to `config/secrets.local.env` (untracked).
2) Fill in demo keys:
   - `BINGX_ENV=demo`
   - `BINGX_DEMO_API_KEY`
   - `BINGX_DEMO_API_SECRET`
   - optional `BINGX_RECV_WINDOW`
   - optional `SCALPER_SECRETS_PASSPHRASE` (only if using encrypted store)
3) Supervisor injects this env file into the bot when `bot.env_file` is set in `config/supervisor.yaml` (e.g. `config/secrets.local.env` or `runtime/secrets.env`).

Helpers:
- `scripts/secrets.ps1` and `scripts/secrets.sh` load the env file into the current shell (no files are written).

Demo order safety switches (default OFF):
- `config/bot.yaml` → `bingx_demo.allow_trading_demo: false`
- `config/bot.yaml` → `bingx_demo.allow_place_test_order: false`
- optional env: `QE_DEMO_PLACE_TEST_ORDER=1` (enables a one-off demo test order path)

To run BingX demo:
- Set `app.mode: demo`
- Set `app.exchange: bingx_swap`
- Keep trading disabled unless you explicitly enable it

Validation (manual):
1) Create `config/secrets.local.env` (ignored by git).
2) Start: `python QuantumEdge.py start`
3) Check bot status: `GET http://127.0.0.1:8765/api/v1/bot/status`
4) Tail logs: `logs/bot.log`

Optional demo test order:
- Set `bingx_demo.allow_place_test_order: true` or `QE_DEMO_PLACE_TEST_ORDER=1`
- Restart bot: `POST http://127.0.0.1:8765/api/v1/bot/restart`

All commands assume a single root `.venv` and use `QE_ROOT` for resolution.
