# QE_OPT_P9_TF_1D_NO_1M_TECHNICAL_INDICATORS_AND_LIVE_CONSOLE_RUNNER_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P9_TF_1D_NO_1M_TECHNICAL_INDICATORS_AND_LIVE_CONSOLE_RUNNER_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-direct-cli-sizer-session
BASE_SHA=8daa099790b0404aec794b24a41b69d3ed6890e2
TARGET_BRANCH=feat/qe-direct-cli-sizer-session
WORKTREE_CLEAN=true

DEFAULT_TFS_UPDATED_TO_1D_4H_1H_15M_5M=true
ONE_MINUTE_TF_DROPPED_BY_DEFAULT=true
INDICATORS_RSI_MACD_BB_ADX_DIV_READY=true
INDICATORS_ZERO_LOOKAHEAD_VERIFIED=true
AGY_RESUME_FAIL_SOFT_READY=true
LIVE_CONSOLE_DASHBOARD_READY=true
P0_LOCAL_SP_TOKENS=4358
P2_STAGE2_LOCAL_SP_TOKENS=3908
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_PHASE9=6 passed
PYTEST_FULL_SUITE_NEW_CORE=145 passed, 1 skipped in 22.00s
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

NEXT_GATE=OPERATOR_FOREGROUND_1H_LIVE_VST_SESSION
```

## 2. Modified & Created Files

- `src/quantum_edge_core/execution/market_context_serializer.py` (modified):
  - Added `"1d": 86_400_000` to `TF_STEP_MS` (preserving `"1m": 60_000` backward compatibility), defined `STRATEGIC_TFS = ("1d", "4h")`, `TACTICAL_TFS = ("1h", "15m", "5m")`, `DEFAULT_ALL_TFS = ("1d", "4h", "1h", "15m", "5m")`, and `resolve_tf_hierarchy()` for seamless legacy 1m-fixture fallback.
  - Added strictly causal numpy/pandas indicators: `_compute_rsi_wilder()`, `_compute_macd()`, `_compute_bollinger()`, `_compute_adx_wilder()` (using `np.divide(..., where=...)` with zero divide warnings), and `_detect_causal_divergence()` (confirmed swing pivots only, zero look-ahead).
  - Extended `build_structural_profile()` with `rsi14`, `macd_hist_bps`, `macd_cross`, `bb_pos`, `bb_bw_bps`, `adx14`, `di_diff`, `rsi_div`, `macd_div`, and added `encode_structural_profile_wire()` for compact token-efficient prompt rendering.
  - Updated `serialize_single_asset()` and `render_candidate_5tf_webp()` to use `resolve_tf_hierarchy()`.
- `src/quantum_edge_core/execution/bingx_vst_adapter.py` (modified):
  - Updated `fetch_full_vst_context()` default `timeframes` to `("5m", "15m", "1h", "4h", "1d")` (dropped `"1m"`). Verified `analytics_enricher.py` falls back seamlessly to `"5m"`.
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py`, `prefix_context_assembler.py`, & `prompts.py` (modified):
  - Updated `PreFetchCoordinator.fetch_all()` to `("5m", "15m", "1h", "4h", "1d")`.
  - Updated `_build_p1_slow()` to cache strategic `("1d", "4h")` macro profiles (with legacy `("4h", "1h")` fallback).
  - Added shared `format_tactical_indicators_line()` (`TAC_IND|...`) to Stage 1 `P2`, and updated `serialize_stage2_candidate()` to emit `OHLCV_TICK_V1` on tactical `("1h", "15m", "5m")`.
  - Updated `SYSTEM_PROMPT_P0_CORE` for `1d/4h` strategic + `1h/15m/5m` tactical hierarchy and causal `PROF_*` indicators (`P0_LOCAL_SP_TOKENS = 4358 >= 4096`).
- `src/quantum_edge_ml/model_router/backends/transport.py` (modified):
  - Added fail-soft retry in `AgyDirectCliTransport.generate()`: if a call with `--resume <session_id>` fails with a non-timeout error, clears the cached session id and immediately retries once without `--resume`.
- `tools/qe_harness_cli.py` (modified):
  - Updated `context --timeframes` default to `"5m,15m,1h,4h,1d"` and `_build_offline_mock_bundle` to generate `1d/4h/1h/15m/5m`.
- `tools/run_vst_session.py` (modified):
  - Added `--reuse-session / --no-reuse-session` (default `--reuse-session`), `--report-out`, `--sync-gdrive`, graceful `SIGINT`/`SIGTERM` (`Ctrl+C`) shutdown, and real-time foreground console dashboard (`flush=True`) showing `1d/4h` indicators, `FORECAST_V1`, Stage 1/2 decisions, `ConvictionPositionSizer`, exchange status, and cumulative `UsageLedger`.
- `tests/test_phase9_tf_1d_indicators_and_console.py` (created):
  - 6 tests verifying causal indicators & zero look-ahead leakage, bullish/bearish divergences, `1d/4h/1h/15m/5m` hierarchy without `1m`, fail-soft `agy --resume`, `P0`/`P2_S2` token thresholds, and live foreground console + Markdown export.

## 3. Architectural Highlights

- **Zero-Leakage Causal Indicators & Wire Efficiency**: All 5 indicator families (`RSI(14)`, `MACD(12,26,9)`, `BB(20,2)`, `ADX(14)`, confirmed-pivot `BULL`/`BEAR`/`NONE` divergences) are computed strictly backward on `bars[:t+1]` (`0.00e+00` mutation from future bars `t >= 60`). Compact wire encoding `|ind=rsi=...,mh=...,mc=...,bb=...,bw=...,adx=...,di=...,div=.../...` preserves `multiscale >= 15.0x` compression and keeps `P0 + P1 = 8,647 <= 9,500` SP tokens.
- **Fail-Soft `--resume`**: Enables testing server-side KV cache reuse on Stage 1 -> Stage 2 via `agy --resume` while guaranteeing 0% cycle failure if `agy` rejects a stale conversation ID.

## 4. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml/model_router src/quantum_edge_ml/forecasting tools/qe_harness_cli.py tools/run_vst_session.py`: **PASS** (exit 0).
- `tests/test_phase9_tf_1d_indicators_and_console.py`: **6 passed**.
- Full 21-file offline suite (`--hypothesis-seed=42`, including `tests/test_zero_duplication_guards.py`): **145 passed, 1 skipped** in `22.00s` (`0` failures).
- Local SP Token measurements on 5-TF (`1d/4h/1h/15m/5m`) bundle:
  - `P0_LOCAL_SP_TOKENS = 4358` (`>= 4,096`)
  - `P1_LOCAL_SP_TOKENS = 4289` (`P0 + P1 = 8,647 <= 9,500`)
  - `P2_STAGE2_LOCAL_SP_TOKENS = 3908` (`>= 2,050`)

## 5. Operator Foreground 1-Hour Live VST Session Command

```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/run_vst_session.py \
  --live-vst \
  --transport agy-direct \
  --reuse-session \
  --model gemini-4-carbon \
  --symbols BNBUSDT,LINKUSDT,SUIUSDT \
  --duration-minutes 60.0 \
  --cycle-interval-sec 180.0 \
  --max-margin-vst 1000 \
  --max-leverage 50 \
  --max-open-positions 3 \
  --max-notional-vst 50000 \
  --close-on-exit \
  --report-out reports/tasks/QE_OPT_LIVE_1H_SESSION_REPORT.md \
  --sync-gdrive
```

## 6. Compliance & Safety Checklist

- `GIT_PUSHED=false` (local commit on `feat/qe-direct-cli-sizer-session` only).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` untouched; AST Zero-Duplication Guards **100% PASS**.
