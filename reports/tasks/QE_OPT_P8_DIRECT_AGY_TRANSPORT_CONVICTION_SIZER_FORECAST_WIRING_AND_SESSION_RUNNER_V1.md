# QE_OPT_P8_DIRECT_AGY_TRANSPORT_CONVICTION_SIZER_FORECAST_WIRING_AND_SESSION_RUNNER_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P8_DIRECT_AGY_TRANSPORT_CONVICTION_SIZER_FORECAST_WIRING_AND_SESSION_RUNNER_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=7dd858ec9b80bc8eb1c383bc51f5c8744c745bbc
TARGET_BRANCH=feat/qe-direct-cli-sizer-session
WORKTREE_CLEAN=true

AGY_DIRECT_CLI_TRANSPORT_READY=true
AGY_SESSION_RESUME_FLAG=--resume
CONVICTION_POSITION_SIZER_READY=true
RISK_LIMITS_IN_P2_AND_AUTO_CLAMP_READY=true
FORECAST_PIPELINE_WIRED_IN_CYCLE=true
PERSISTENT_SESSION_RUNNER_READY=true
PERSISTENT_RUNNER_PREFIX_HASH_STABLE=true
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_PHASE8=5 passed
PYTEST_FULL_SUITE_NEW_CORE=139 passed, 1 skipped in 19.51s
TOTAL_FAILED=0

GIT_PUSHED=false
EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
LIVE_READS=0
LIVE_WRITES=0
PROVIDER_GETS=0
PROVIDER_MUTATIONS=0
SERVICE_RESTARTS=0
DEPLOYED=false
REAL_TRADING=false

NEXT_GATE=RUN_LIVE_15MIN_VST_SESSION_ON_BNB_LINK_SUI_DIRECT_AGY
```

> **Branch Base Note:** Preflight inspection confirmed `origin/main` was at `94be91c` without Phases 0–7 (`7dd858e`). Per explicit operator confirmation, `feat/qe-direct-cli-sizer-session` was branched cleanly from `origin/feat/qe-antigravity-hermes-bridge` (`BASE_SHA=7dd858ec9b80bc8eb1c383bc51f5c8744c745bbc`).

## 2. Modified & Created Files

- `src/quantum_edge_ml/model_router/backends/transport.py` (modified):
  - Added `AGY_SESSION_RESUME_FLAG = "--resume"` (discovered via `/home/layman/.local/bin/agy --help`: `--resume` / `--conversation`, `--effort`, `--print-timeout`, `--sandbox`, `--disable-slash-commands`, `--input-format text`, `--output-format json`).
  - Added `AgyDirectCliTransport(LLMTransport)` invoking `agy` directly via `asyncio.create_subprocess_exec` inside a RAM-backed workspace (`get_ram_tmp_dir("agy_stateless_ws")`), eliminating the HTTP proxy daemon on port `8045`. Parses all 5 native `usage` fields (`input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`), records `UsageRecordV1` directly into `UsageLedger` (with `NULL=UNKNOWN` and `is_censored=True` on timeouts), supports optional `--resume` session chaining (`reuse_session=True`), and supports `runner_override` for deterministic offline testing.
  - Preserved `AgyProxyTransport` intact for fallback compatibility.
- `src/quantum_edge_core/execution/position_sizer.py` & `execution/__init__.py` (created + re-exported):
  - Added `SizingResult` and `ConvictionPositionSizer` (non-forbidden naming compliant with `test_zero_duplication_guards.py`) computing `risk_score in [0, 1]` from 6 orthogonal factors (`regime_alignment_risk` 0.20, `volatility_tail_risk` 0.20, `microstructure_risk` 0.20, `liquidity_spread_risk` 0.15, `barrier_edge_risk` 0.15, `model_confidence_risk` 0.10), scaling notional via `conviction = max(0.10, min(0.95, 1.0 - risk_score))` up to `50,000 VST`, enforcing `safe_leverage = min(max_leverage, floor(0.75 / sl_dist))` (SL strictly inside liquidation buffer), and capping max SL loss to `(10%..50%)` of `1,000 VST` isolated margin.
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py` & `prefix_context_assembler.py` & `prompts.py` (modified):
  - Added shared `extract_account_risk_limits()` injecting `max_margin_vst`, `max_leverage`, `max_notional_vst`, and `max_open_pos` into `P2.account.risk_limits` across Stage 1 and Stage 2.
  - Wired `position_sizer: ConvictionPositionSizer | None` into `DeterministicTradingOrchestrator` to auto-size/clamp `margin_vst` and `leverage` before `HarnessBridge.submit_decision()`, recording `decision_payload["sizing"]`.
  - Added `LocalForecastProvider` caching per-symbol `ForecastPipeline` instances in RAM (`refit_interval_sec=900.0`, fail-soft on short/degenerate bars) to emit live `ForecastBundleV1` (`FORECAST_V1|...`) into `P2` and `ReasoningEffortRouter`.
  - Tracked `PrefixContextAssembler.last_resync_mode` accurately (`RESYNC` -> `LOCAL_RECONSTRUCT` / `CACHE_HIT`) and preserved `P0` constitutional threshold (`4,141 >= 4,096` local SP tokens).
- `tools/qe_harness_cli.py` (modified):
  - Added `--transport agy-direct` as the new default (`agy-proxy`, `gemini-direct`, and `mock` preserved), plus `--enable-forecast/--no-forecast` and `--enable-conviction-sizer/--no-conviction-sizer` (both enabled by default).
- `tools/run_vst_session.py` (created):
  - Persistent single-process RAM-First VST session runner holding `AgyDirectCliTransport` (or `mock`/`agy-proxy`), `PrefixContextAssembler` + RAM `SnapshotStore`, `ThesisMemoryStore`, `UsageLedger`, `LocalForecastProvider`, `ConvictionPositionSizer`, `ReasoningEffortRouter`, `DeterministicTradingOrchestrator`, and a background 5-second `PositionProtectionWatcher`, with per-symbol `--close-on-exit` (default) and RAM session summaries under `/dev/shm/quantumedge/sessions/`.
- `tests/test_phase8_direct_cli_sizer_and_runner.py` (created):
  - 5 unit/integration tests covering `AgyDirectCliTransport` (5 usage fields, `--resume`, `NULL` on timeout), `ConvictionPositionSizer` (low/high risk + `margin_vst=5000.0` C2 auto-clamp), `P2.account.risk_limits` + `FORECAST_V1|`, `LocalForecastProvider`, and multi-cycle persistent `run_vst_session.py` (`prefix_hash_stable=True`, `watcher_runs >= 1`).

## 3. CLI Inspection (`agy --help`) & ConvictionPositionSizer Numerical Examples

- **`agy --help` inspection (0 LLM calls)**:
  - Session resume flags present: `--resume` (alias `-r`, `--conversation`: *"Resume a previous conversation by ID"*) and `--continue` (`-c`).
  - Also supports `--effort` (`low|medium|high|xhigh|max`), `--model`, `--sandbox`, `--disable-slash-commands`, `--input-format text|stream-json`, `--output-format json|stream-json`, `--json-schema`, `--print-timeout`.
  - Recorded `AGY_SESSION_RESUME_FLAG=--resume` and implemented opt-in Stage 1 -> Stage 2 chaining via `reuse_session=True`.
- **`ConvictionPositionSizer` computed examples (`RiskConfig.for_qe_isolated_50x()`):**
  - **Low-risk tight-SL (`BNBUSDT`, `SL=60 bps`, LLM proposed `margin_vst=5000.0`)**:
    `risk_score=0.1658`, `conviction=0.8342`, `notional_vst=41,710.00`, `margin_vst=834.20` (`clamped_from_llm_margin=5000.0`), `leverage=50x`, `est_loss_at_sl_vst=298.23` -> **PASSES `LeanRiskGuard`**.
  - **High-risk wide-SL (`LINKUSDT`, `SL=200 bps`, LLM proposed `margin_vst=900.0`, `leverage=50x`)**:
    `risk_score=0.7108`, `conviction=0.2892`, `notional_vst=9,138.63`, `margin_vst=246.99`, `leverage=37x` (`floor(0.75/0.02)`), `est_loss_at_sl_vst=215.67` -> **PASSES `LeanRiskGuard` (0 `SL_BEYOND_LIQUIDATION_BUFFER` rejections)**.

## 4. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml/model_router src/quantum_edge_ml/forecasting tools/qe_harness_cli.py tools/run_vst_session.py`: **PASS** (exit 0).
- `tests/test_phase8_direct_cli_sizer_and_runner.py`: **5 passed**.
- Full offline suite across 20 test files (`--hypothesis-seed=42`, including `tests/test_zero_duplication_guards.py`): **139 passed, 1 skipped** in `19.51s` (`0` failures).
- Local SentencePiece P0 check: `P0_LOCAL_SP_TOKENS = 4141` (`>= 4,096`).

## 5. Operational Runbook — 15-Minute Live VST Session (Direct AGY, Zero HTTP Proxy)

```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/run_vst_session.py \
  --live-vst \
  --transport agy-direct \
  --model gemini-4-carbon \
  --symbols BNBUSDT,LINKUSDT,SUIUSDT \
  --duration-minutes 15.0 \
  --cycle-interval-sec 180.0 \
  --max-margin-vst 1000 \
  --max-leverage 50 \
  --max-open-positions 3 \
  --max-notional-vst 50000 \
  --close-on-exit
```

## 6. Compliance & Safety Checklist

- `GIT_PUSHED=false` (local commit only on `feat/qe-direct-cli-sizer-session`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- AST Zero-Duplication Guards (`RiskGuard`/`Executor` suffix check, single `ccxt.bingx` adapter, single WebSocket feed, no `cachedContents`) **100% PASS**.
