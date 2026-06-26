# Release Notes

## Unreleased

- Added a SPOT-only hot-path scalper engine with regime/signal/exec/risk loop, L1/L2 book handling, and tests; futures are disabled for this mode.

## [2026-06-23] - BingX Migration, QuestDB ILP, and Bot Decoupling

- **BingX Migration & Binance Removal:** Completely removed Binance dependencies and replaced them with `BingXLiveFeed` for live web-sockets and user data streams.
- **QuestDB ILP Integration:** Switched database ingestion to high-performance Influx Line Protocol (ILP) on port 9009 with a background asynchronous `ilp_writer`. Unified tables into `market_features`, `bot_telemetry`, and `llm_decisions`.
- **QuestDB Schema Management & Data Retention:** Implemented a schema manager and automatic data retention purger to clean up stale DB records.
- **Historical Query Builder & Warm-Up:** Added `QuestDBQueryBuilder` for fetching microstructural indicators, volatility profiles, and VWAP bands. Implemented a warm-up sequence for HFT and DCA bots to pre-populate history before ZMQ SUB activation.
- **Microservices Isolation:** Decoupled bots into independent processes:
  - `ai_scalper_bot`: Re-enabled the XGBoost-based ML `AdaptiveGridStrategy` with an async 2-second telemetry heartbeat.
  - `dyn_dca_bot`: Mathematical grid engine (`DCAEngine`), EWMA Volatility Oracle, and L2 aggregator for Whale Wall detection with a front-running "Magnet Effect".
  - `lock_bot`: Fixed ZMQ port conflicts by reallocating telemetry ports.
- **Unified Orchestration (`QuantumEdge.py`):** Deprecated the legacy `SupervisorAgent`. Orchestrator runs `MarketDataHub -> Dashboard API Bridge -> AIScalper -> DynDCA` sequentially. Implemented a port-guarding ZMQ Guard to clean up zombie connections prior to startup.
- **Hermes Agent Integration & MCP Bridges:**
  - Added ZMQ-MCP status and policy injection bridge (`zmq_mcp_bridge.py`).
  - Added Data-MCP Bridge for live market snapshots and DB queries.
  - Implemented a FastAPI-based Dashboard API on port 8765 for TradingView lightweight charts.
  - Upgraded Google Gemini backend to `google-genai` SDK with structured Pydantic schemas.
- **System Hardening & Split-Brain Fixes:** Enforced a Single Source of Truth (SSOT) for market prices using Hub's mid-price. Fixed float-decimal type collision in drawdown calculation. Hardened DynDCA TP logic to prevent loss-making executions.


## Stage 0 - Hardening

- Removed tracked secrets and added `.env.example`.
- Strengthened `.gitignore` for secrets/logs/runtime artifacts.
- Added secret masking and denylist in project scanning.
- Routed stage writes through safety policy and shared write engine.
- Added run lock, diagnostics, chunking fixes, and CI tests.
- Checks: `python meta_agent.py diag`, `python -m pytest -q`.

## Stage 1 - Task Contract + run_task

- Introduced TaskSpec/Report contract with YAML/MD parsing.
- Implemented `meta_core.run_task` artifact pipeline.
- Added exit codes and CLI `run-task` subcommand.
- Added tests for policy gating and run_task behavior.
- Documented TaskSpec and runtime layout.
- Checks: `python meta_agent.py run-task --task examples/tasks/001_refactor_small.yaml`.

## Stage 2 - Service-ready CLI

- Added health/status/watch commands and runtime logging.
- Added scheduler ops templates and operations docs.
- Added JSON output options and CLI flags.
- Added tests for watch/health/JSON output.
- Checks: `python meta_agent.py health`, `python meta_agent.py watch --inbox runtime/inbox`.

## Stage 3 - Shadow + Quality Gates

- Added shadow workspace and gate runner.
- Gated allow applies on shadow+gates results.
- Added dry-run and gate_failed exit codes.
- Added tests for gate runner and gated apply behavior.
- Updated docs and examples for gates.
- Checks: `python meta_agent.py run-task --task examples/tasks/003_shadow_gates_demo.yaml`.

## Stage 4 - Off-market Scheduler

- Added ScheduleSpec and stateful scheduler with backoff.
- Implemented crash recovery and inbox processing reuse.
- Added scheduler CLI commands and tests.
- Added example schedule and docs.
- Checks: `python meta_agent.py run-scheduler --once`.

## Stage 5 - Control Center UI

- Added local UI server and static UI for tasks/runs/schedules.
- Added manual approve/apply with shadow+gates re-run.
- Added projects registry for UI defaults.
- Persisted changeset.json for safe approvals.
- Added UI docs and tests.
- Checks: `python meta_agent.py ui`.

## Stage 6 - Documentation

- Rewrote README and operations runbook.
- Added architecture diagrams and scheduler docs.
- Added security guidance and release notes.
- Added docs check in CI.
- Checks: `python meta_agent.py diag`, `python -m pytest -q`.

## Stage 7 - Release Pack

- Added packaging metadata and console entrypoint.
- Added smoke E2E script and CI smoke job.
- Added structured run events (`events.jsonl`) and dump-run command.
- Added upgrade guide and lint job.
- Checks: `meta-agent diag`, `python tools/smoke_e2e.py`.
