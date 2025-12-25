# QuantumEdge Audit (Pro Mode)

## 0) Executive Summary
Partial: current runtime is mock-data only, model-incomplete, and limited to paper/demo execution. Production trading is blocked by missing models, disabled live data, and incomplete risk/ops controls.

- Runtime uses mock websocket regardless of config; no live market connectivity (`bot/run_bot.py:_event_stream`).
- Required models missing; require_models=true causes engines to skip symbols (only `storage/models/signal_xgb_BTCUSDT_h1.json` present; `bot/run_bot.py:main`).
- No live executor implemented; only PaperTrader and Binance testnet are wired (`bot/run_bot.py:main`, `bot/trading/executor.py:BinanceDemoExecutor`, `bot/trading/paper_trader.py:PaperTrader`).
- Loss-streak/overtrading controls never update stats (no `TradeStats.record` calls; `bot/engine/decision_engine.py`), making those limits ineffective.
- Scalp mode guards block entries with default depth/spread requirements against mock events (`bot/trading/execution_mode.py:ScalpExecutionMode.execute_trade`), so scalp trades will skip.
- Orders are market-only with no TP/SL or idempotency; partial fills and retries are unhandled (`bot/trading/execution_mode.py:NormalExecutionMode`, `bot/trading/executor.py:process`).
- Data persistence writes every tick to disk without rotation (JSON + CSV) in the hot loop (`bot/market_data/data_manager.py:save_trade`), risking growth and latency.
- LLM risk moderator is heuristic-only and disabled by config (`bot/ai/risk_moderator.py`, `bot/run_bot.py:main` llm_enabled default false).
- Backtesting/LLM client/WS connectors are present but empty placeholders (`bot/backtester/*.py`, `bot/ai_llm/*.py`, `bot/market_data/binance_ws.py`).
- Secrets committed in plaintext (`config/secrets.env`, `backup_secrets/secrets.env`); encrypted secrets still tracked and require runtime passphrase (`bot/core/config_loader.py`).

## 1) Repo Map (module inventory)
| Module/Area | Paths | Status | Evidence | Notes |
| --- | --- | --- | --- | --- |
| Entrypoints & loops | `run_bot.py`, `bot/run_bot.py:main`, `bot/run_service.py:main` | 🟡 partial | `_event_stream`, `main` | Service runner exists; default run uses mock stream and blocks without secrets/models. |
| Config & secrets mgmt | `bot/core/config_loader.py`, `bot/core/secret_store.py`, `config/settings.yaml` | 🟡 partial | `Config._load_secrets`, `get_runtime_password` | Requires secrets.enc + passphrase; plaintext secrets committed; QE_CONFIG_PATH override supported. |
| Market data ingestion | `bot/market_data/mock_ws_manager.py`, `bot/market_data/ws_manager.py`, `bot/market_data/offline_simulator.py` | 🟡 partial | `MockWSManager.stream`, `WSManager.connect` | Runtime forces mock; Binance WS present but not wired; offline simulator exists. |
| Feature building (online) | `bot/ml/signal_model/online_features.py:OnlineFeatureBuilder` | ✅ confirmed | feature computation pipeline, warmup gate | Used in `bot/run_bot.py` per symbol. |
| Dataset building (training) | `bot/ml/signal_model/dataset_builder.py:DatasetBuilder`, `data/ticks/*.csv` | ✅ confirmed | build(), schema normalization | Training script uses this; alignment with online features. |
| ML models (load/predict) | `bot/ml/ensemble.py:EnsembleSignalModel`, `bot/ml/signal_model/model.py:SignalModel` | 🟡 partial | loads horizons 1/5/30, warns on missing files | Only h1 BTC model exists; require_models=True stops trading when others missing. |
| Decision engine / strategy | `bot/engine/decision_engine.py:DecisionEngine`, `bot/engine/regime_policy.py` | 🟡 partial | decide() gating | Loss/overtrade stats not updated; regime/style tagging present. |
| Execution layer (paper/demo/live) | `bot/trading/paper_trader.py`, `bot/trading/executor.py:BinanceDemoExecutor`, `bot/trading/execution_mode.py` | 🟡 partial | process(), submit_order(), execute_trade() | Only paper + Binance testnet; no live executor; scalp policy mostly logging. |
| Risk engine / limits | `bot/ai/risk_moderator.py`, `bot/risk/scalp_guards.py` | 🟡 partial | LLMRiskModerator.evaluate(), ScalpGuard.can_enter() | LLM disabled by config; guards track counts only; no kill switch. |
| Storage/logging | `bot/market_data/data_manager.py`, `bot/core/logging_setup.py`, `state/bot_status.json` | 🟡 partial | _append_tick(), setup_logging() | Per-tick file writes; rotating logs only via run_service; no rotation for data. |
| Metrics/health/status output | `bot/ops/status_writer.py:BotStatusWriter`, `bot/supervisor_client.py` | 🟡 partial | update(), send_heartbeat_if_due() | Status file only if run_service provides writer; supervisor heartbeat optional. |
| Backtesting/sandbox tools | `bot/sandbox/offline_loop.py`, `bot/backtester/*.py` | 🧱 stub | offline_loop uses outdated DecisionEngine signature; other files empty | Present but not wired/obsolete. |
| Installation/ops scripts | `bot/run_service.py`, `setup_bot.ps1`, `build_runbot_exe.ps1`, `run_bot.cmd` | 🟡 partial | run_service wiring; setup uses ExecutionPolicy Bypass | Windows-focused; no Linux entrypoint; cmd scripts point to ws_manager not trading loop. |
| Tests | `test_features.py` | 🧱 stub | prints features | No assertions or CI. |

## 2) Runtime wiring (observed path)
- User entrypoint: `run_bot.py` → imports `bot/run_bot.py:main` after setting supervisor flag; `bot/run_service.py` offers service wrapper with logging + status.
- Config load: `bot/core/config_loader.Config` reads `config/settings.yaml`, immediately decrypts `config/secrets.enc` via `get_runtime_password` (env `SCALPER_SECRETS_PASSPHRASE` or GUI). Missing passphrase exits.
- Mode selection: `config.app.mode` (default "demo") → demo uses `BinanceDemoExecutor`, paper uses `PaperTrader`; any other mode raises error. `execution.mode` chooses `NormalExecutionMode` or `ScalpExecutionMode` (requires `execution.scalp.enabled`).
- Data source: `_event_stream` ignores config except for warning; always yields `MockWSManager.stream` trades for configured symbols (defaults BTCUSDT). No real WS path used.
- Per-symbol wiring: `EnsembleSignalModel` (horizons 1/5/30) + `OnlineFeatureBuilder` + `DecisionEngine`; attaches `LLMRiskModerator`, `execution_mode`, and trader per symbol. If models missing and `ml.require_models` true, engine skipped.
- Main loop: for each mock trade → `DataManager.save_trade` writes JSON/CSV → build features → `ensemble.predict` → optional LLM risk → `DecisionEngine.decide` → `execution_mode.execute_trade` (market-only) → supervisor order evaluation if enabled → optional `enforce_time_stop` (scalp) → periodic stats print.
- Heartbeats/ops: `SupervisorClient.send_heartbeat_if_due` called if configured; status JSON written via `BotStatusWriter.update` when provided (run_service). Snapshot monitor optional via `supervisor_snapshots` config.
- Shutdown: stop_event or KeyboardInterrupt; cancels snapshot monitor; writes status is_running false.

## 3) Execution modes
- Paper: enable via `app.mode: paper`; uses `bot/trading/paper_trader.py` with 1-unit default size, simulated fees/latency; no exchange validation; no TP/SL.
- Demo (Binance testnet): default `app.mode: demo`; `bot/trading/executor.py:BinanceDemoExecutor` enforces testnet endpoints and symbol allowlist; order sizing = equity * `position_pct` capped by `max_notional_per_trade`; MARKET/optional LIMIT if price passed; no live exchange mode implemented.
- Scalp vs Normal: choose via `execution.mode` + `execution.scalp.enabled`; scalp uses `ScalpExecutionMode` + `OrderPolicy` + `ScalpGuard`. Default depth (`min_orderbook_depth_usd`=5000) and spread checks block entries with mock events; stops computed but not placed; time-stop closes via market. Normal mode just maps enter/exit to market process.

## 4) Trading safety & correctness audit
- Order sizing: demo mode sizes by equity percentage with min_notional normalization (`bot/trading/executor.py:_compute_entry_qty`, `_normalize_qty`); paper uses fixed 1 unit; no cap for paper/live beyond hardcoded 1 unit.
- TP/SL: none for normal mode; scalp computes targets but does not place protective orders (`bot/trading/execution_mode.py:_compute_stop_targets` only logs). No OCO or trailing stops.
- Spread/depth/latency: scalp checks spread/depth against last_event but mock events lack bid/ask so spread=0 and depth≈price*qty (<5000), causing `insufficient_depth` skips. No latency compensation beyond paper trader sleep.
- Partial fills: no handling; `OrderPolicy.place_scalp_order` assumes filled; BinanceDemoExecutor ignores fills and assumes success on API response.
- Idempotency/retries: no client order IDs or retry logic; submit_order is one-shot without backoff (`bot/trading/executor.py:submit_order`).
- Reconnection/state recovery: main loop has no persistence of positions; demo executor has best-effort `sync_positions` unused; crash recovery absent.
- Kill switch/global pause: none beyond manual stop_event; risk_on_error bypass defaults to allow trading when Supervisor unreachable.
- Max drawdown/daily loss/cooldown: placeholders in LLMRiskModerator thresholds, but paper/demo traders never feed pnl into guards; `TradeStats` loss streak never updated.
- Leverage/liq awareness: not set; demo exchange uses default leverage; no margin checks.

## 5) Data & storage audit
- Tick/depth storage: `bot/market_data/data_manager.py` writes per-event JSON files under `config.app.data_path` (`data/trades`, `data/orderbooks`) and appends ticks to `data/ticks/{symbol}_stream.csv`. No rotation/retention.
- File formats: JSON (one file per event) and CSV with header; growth risk high under real streams; writes occur in event loop without async IO.
- TSDB/DB: none; `storage/db` directory empty.
- Status/metrics: `state/bot_status.json` schema `{ts,is_running,is_trading,open_positions,open_orders,mode,last_error}` when BotStatusWriter used; no metrics emitter.

## 6) ML audit
- Training: `bot/ml/signal_model/train.py` builds datasets via `DatasetBuilder.build` and trains XGBoost models per horizon; saves to `storage/models/signal_xgb_{symbol}_h{h}.json` and datasets to `storage/datasets`.
- Feature schema: shared `bot/ml/feature_schema.py` used by both dataset_builder and online_features (fields align, including regime_tag).
- Model loading: `EnsembleSignalModel` loads configured horizons; missing files emit warnings; `ml.require_models` true aborts trading when no models (`bot/run_bot.py`). Only BTCUSDT h1 model exists, so ensemble edges mostly empty.
- Ensemble aggregation: equal weights; no calibration/thresholding beyond DecisionEngine min_conf/min_edge.
- Probability thresholds: DecisionEngine uses meta_edge vs min_conf; LLM risk require_edge configurable; scalp uses min_prob_up/min_edge.
- Regime features: regime_tag derived from vol/ema slope in online/dataset pipelines.
- Versioning: none; models overwrite; no checksum/registry.

## 7) Security audit
- Plaintext secrets committed: `config/secrets.env`, `backup_secrets/secrets.env` contain Binance/OpenAI keys. Encrypted `config/secrets.enc` also committed despite .gitignore.
- Secrets loading requires runtime passphrase; in supervisor mode GUI prompt forbidden → missing env halts start (`bot/core/config_loader.py`, `bot/core/secret_store.py`).
- Installer uses `Set-ExecutionPolicy Bypass` (`setup_bot.ps1`), which is risky; no checksum validation for python installer binary.
- No `.gitignore` coverage for `state/` or `logs/` growth; compiled `run_bot.exe` present.
- No dependency pinning beyond `requirements.txt` minimal; no supply-chain verification.

## 8) Pro mode readiness checklist
- Normal/scalp execution: 🟡 Normal works on mock/paper/demo; scalp blocks due to depth/spread and no stops. Needs real orderbook feed + stop placement + depth config to reach ✅.
- AI signal filter: 🟡 LLMRiskModerator exists but heuristics only and disabled by default; enable `app.llm_enabled` and integrate real LLM backend/telemetry for ✅.
- Strategy modes: 🟡 DecisionEngine primary; scalp policy partial; backtesting stubs. Need functional scalp pipeline + loss/overtrade stats wiring.
- Metrics/status file: 🟡 BotStatusWriter works only via run_service; no metrics/alerts. Need always-on status + health endpoints.
- Production-ready entrypoints: ❌ Default run uses mock data and fails without passphrase/models; WS/live path not wired. Need live WS ingestion + configurable entry (paper/demo/live) + non-interactive secrets flow.
- Ops templates (Windows/Linux): 🟡 Windows scripts only; Linux/systemd absent; installers bypass execution policy. Need cross-platform service templates and safer install.

## 9) Concrete next actions (non-code)
- Harden secrets: remove committed keys (`config/secrets.env`, `backup_secrets/secrets.env`), rotate credentials, ensure gitignore enforcement (complexity M).
- Provide non-interactive secrets bootstrap in CI/ops using `tools/init_secrets.py` + env vars; document Supervisor mode requirements (S).
- Wire live market data path in `bot/run_bot.py` to `WSManager` with config gating, keeping mock as fallback; add reconnection/backoff plan (M).
- Complete model set: train horizons 1/5/30 for configured symbols via `bot/ml/signal_model/train.py`; store artifacts in `storage/models` with versioning plan (M).
- Enable trading only when models loaded: add startup validation/reporting and observer fallback toggles in config/docs (S).
- Implement TP/SL and idempotent order submission for demo/live paths; define client order IDs and retry/backoff policy in `bot/trading/executor.py` (L).
- Make scalp mode usable: align depth/spread thresholds to available data, place actual stop orders, and log guard outcomes; consider disabling scalp by default until ready (M).
- Wire TradeStats updates from executors so loss/overtrade gates in `DecisionEngine` function; define drawdown tracking (M).
- Add kill-switch/circuit-breaker (daily loss, max drawdown, max exposure) controllable via config + Supervisor responses (M).
- Reduce hot-loop I/O: batch or buffer tick persistence, add retention/rotation policy for `data/trades` and `data/ticks` (M).
- Formalize ops entrypoints: service scripts for paper/demo/live, Linux/systemd unit, and Windows NSSM template; align run_bot.cmd to actual trading loop (M).
- Observability: always emit status/health to `state/bot_status.json` + stdout, add metrics hook (Prom/StatsD) and structured logging (M).
- Security review of installer scripts: remove `ExecutionPolicy Bypass`, verify Python installer checksum, document minimal privileges (M).
- Backtesting readiness: replace stub files in `bot/backtester` and fix `bot/sandbox/offline_loop.py` signature mismatch; create reproducible backtest harness (L).
- Document runtime wiring and configs (mode selection, supervisor, llm, storage) in README/Documentation for operators (S).

