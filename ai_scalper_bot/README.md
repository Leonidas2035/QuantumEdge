# QuantumEdge / ai_scalper_bot

## Runtime vs research
- Runtime path: `ai_scalper_bot/bot` (live market data, execution, decisioning, hard risk, online ML inference).
- Research/offline tooling moved to `SupervisorAgent/research/...` with thin wrappers left in `ai_scalper_bot` for one stage.

## SupervisorAgent heartbeat integration
- The bot can send runtime heartbeats to SupervisorAgent (`QE_ROOT/SupervisorAgent`) to expose uptime, PnL, and equity in real time.
- Configure under `config/settings.yaml` -> `supervisor` section:
  - `enabled`: turn integration on/off.
  - `base_url`: SupervisorAgent API base (default `http://127.0.0.1:8765`).
  - `api_token`: optional shared secret (`X-API-TOKEN`).
  - `heartbeat_interval_s`: minimum seconds between heartbeats.
  - `timeout_s`: HTTP timeout.
  - `on_error`: behavior when SupervisorAgent is unreachable (currently logs and continues).
- Heartbeats are sent from the main trading loop and include mode, uptime, realized/unrealized PnL, simple equity, open positions, and last tick timestamp.

## SupervisorAgent risk gateway
- Each order can be pre-checked by SupervisorAgent via `/api/v1/risk/evaluate`.
- Enable via `supervisor.risk_enabled` in `config/settings.yaml`.
- `risk_on_error` controls fail-open vs fail-closed when SupervisorAgent is unavailable (`bypass` | `block`).
- `risk_log_level` tunes verbosity of decision logs.
- When Supervisor denies an order (`allowed=False`), the bot skips sending it and logs the reason.

## Secrets management (non-interactive starts)
- Secrets are decrypted using `SCALPER_SECRETS_PASSPHRASE` or environment variables. For paper/mock runs with `app.llm_enabled=false`, the bot will start without prompting for a passphrase. Demo/live modes or LLM-enabled runs require secrets.
- To generate `config/secrets.enc`: set `SCALPER_SECRETS_PASSPHRASE` and the required API keys in env, then run `python tools/init_secrets.py`. The encrypted file is **not** tracked in git.
- If secrets are required but missing, startup fails with a clear error message (no stacktrace). Provide either the encrypted secrets file with the passphrase or set API keys via environment variables.
- Secret guard: run `python tools/check_secrets.py` locally/CI to fail if suspicious keys or `*secrets*` files are present in the repo.
- Modes vs. secrets:
  - **No secrets required:** `app.mode=paper/mock` with `app.llm_enabled=false`.
  - **Secrets required:** `app.mode=demo/live` or `app.llm_enabled=true` (needs env keys and/or `config/secrets.enc` + `SCALPER_SECRETS_PASSPHRASE`).

## Data storage and rotation
- Default behavior: CSV ticks are buffered and appended; JSON-per-event logging is disabled to avoid file explosion.
- JSONL logging can be enabled in `config/settings.yaml` under `storage`:
  - `save_trades` / `save_orderbook_json` enable JSONL for trades/orderbooks.
  - `max_jsonl_size_mb`, `max_jsonl_minutes` control rotation; `retention_days` controls cleanup.
  - `flush_batch` and `flush_interval_seconds` control buffered flush frequency.
- Files live under `app.data_path` (default `./data/`) with rotation per size/age; old files are removed after retention.

## ML models
- Models are stored under `storage/models/` with a registry in `storage/models/registry.json` (entries: symbol, horizon, created_at, feature_schema_hash, model_path).
- Train all configured symbols/horizons with:
  - `python -m SupervisorAgent.research.offline.signal_model.train_all --symbols BTCUSDT,ETHUSDT --horizons 1,5,30 --data data/ticks --min-rows 1000`
  - (compat wrapper) `python -m bot.ml.signal_model.train_all ...`
- On startup, the bot checks required models (config `ml.horizons`) and blocks trading if `ml.require_models=true` and any are missing. A readiness table is printed at launch.
 - Published runtime models (recommended) live under `runtime/models/<symbol>/<horizon>/current/` with a `manifest.json`.
 - Set `ml.model_source: runtime` (default) to load published runtime models and validate sha256.

## Backtesting & tests
- Offline backtest: `python -m SupervisorAgent.research.sandbox.offline_loop --symbol BTCUSDT --ticks-path data/ticks/BTCUSDT_sample.csv`
- (compat wrapper) `python -m bot.sandbox.offline_loop --symbol BTCUSDT --ticks-path data/ticks/BTCUSDT_sample.csv`
- Run tests: `python -m pytest`
- Recommended pre-deploy workflow: run pytest + a quick offline backtest on recent ticks to confirm risk limits and data writers behave.
