# QE_OPT_P6_CAUSAL_FORECAST_PIPELINE_REASONING_ROUTER_AND_ACCEPTANCE_MATRIX_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P6_CAUSAL_FORECAST_PIPELINE_REASONING_ROUTER_AND_ACCEPTANCE_MATRIX_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=6c90c3b650d102ce34a194450079d48e0f3db7ef
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
CAUSAL_HMM_FUTURE_LEAKAGE_DIFF=0.00e+00
PURGED_WALK_FORWARD_VERIFIED=true
EGARCH_ARCH9_SIMULATION_OK=true
ACI_CQR_RAW_VOL_SHIFT_COVERAGE=0.4385
ACI_CQR_ADAPTED_VOL_SHIFT_COVERAGE=0.8096
FORECAST_BUNDLE_LOCAL_SP_TOKENS=227
REASONING_ROUTER_HYSTERESIS_CARBON_NEON_OK=true
ACCEPTANCE_MATRIX_12GATES_DRY_RUN=PASS
CLI_RUN_CYCLE_WITH_ROUTER_DRY_RUN=PASS
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE6=9 passed
PYTEST_FULL_SUITE_NEW_CORE=122 passed, 1 skipped in 15.55s
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
NEXT_GATE=LIVE_VST_CANARY_FULL_OPTIMIZED_STACK_VIA_OAUTH_PROXY
```

## 2. Modified & Created Files

- `src/quantum_edge_core/contracts/optimization.py`, `lean.py`, `__init__.py` (modified): added canonical `ForecastBundleV1` (`symbol`, `horizon`, `regime_probs`, `regime_maturity`, `regime_entropy`, `cond_vol_bps`, `vol_zscore`, `return_quantiles_bps`, `barrier_probs`, `expected_mfe_bps`, `expected_mae_bps`, `tsfm_disagreement`, `disagreement_level`, `aci_alpha_t`, and `to_compact_prompt_str()` rendering a `227`-token `FORECAST_V1` wire line) with non-invasive re-exports.
- `src/quantum_edge_ml/forecasting/pipeline.py` & `__init__.py` (created):
  - `purged_walk_forward_splits()` (enforcing `max(train_idx) + label_horizon < min(test_idx)` with purge + embargo);
  - `compute_triple_barrier_labels()` (volatility-normalized `y_zret`, `{-1, 0, +1}` `y_tb`, `y_mfe_bps`, `y_mae_bps`);
  - `compute_calibration_metrics()` (`brier_score`, `log_loss`, `pinball_loss`, `crps_approx`, 10-bin `ece`);
  - `CausalHMMFilter` (log-space forward-only recursion via `scipy.special.logsumexp`, state ordering `DOWN < RANGE < UP`, `0.00e+00` future leakage);
  - `EGARCHVolatilityModel` (`arch==9.0.0` `EGARCH(1,1,1)` with `method="simulation"` and `np.random.RandomState(seed)` for `horizon_steps > 1`, plus safe fallback on short/constant series);
  - `AdaptiveConformalCalibrator` (ACI + CQR, Gibbs & Candes 2021, `gamma=0.02`, `window=100`, `alpha_target=0.20`);
  - `MultiHeadLGBMForecaster` (7 quantiles `q05..q95` with anti-crossing `np.sort`, 1/2/3-class safe Triple-Barrier classifier, MFE/MAE `q50`/`q90` heads);
  - `ShadowTSFMChallenger` (fail-soft Chronos-2 / Kronos wrapper with zero execution authority);
  - `ForecastPipeline` (`build_causal_features()`, `fit_from_ohlcv()`, `predict_bundle()`).
- `src/quantum_edge_ml/model_router/reasoning_router.py` (created): `ReasoningEffortRouter` implementing `EffortRouterProtocol` (`WEIGHTS`, `compute_complexity()`, `select_level_from_score()`, `select_level()` with `+/-0.05` hysteresis and `clamp_thinking_level()`).
- `tools/qe_harness_cli.py` (modified): wired `ReasoningEffortRouter(model_id=args.model)` by default in `run-cycle` (with `--fixed-thinking-medium` diagnostic override and `stage2_thinking_level` / `effort_router_enabled` in JSON output).
- `tools/verify_acceptance_matrix.py` (created): 12-gate Acceptance Matrix verifier (`moving_block_bootstrap_noninferiority()`, `compute_hold_quality()`, `evaluate_matrix()`, and RAM-only `--dry-run` in `/dev/shm/quantumedge/phase6_eval`).
- `tests/test_phase6_forecast_and_router.py` (created): 9 unit and end-to-end tests covering all Phase 6 invariants.

## 3. Architectural Decisions

- **Strictly Causal Forward-Only HMM & Purged Walk-Forward**: `CausalHMMFilter.filter_causal()` computes $\log \alpha_t = \log(\alpha_{t-1} A) + \log p(O_t | S_t)$ without backward smoothing (`0.00e+00` max abs diff on $t < 50$ under a massive shock at $t \ge 50$), paired with strictly backward rolling features in `ForecastPipeline.build_causal_features()`.
- **`arch==9.0.0` Simulation Compatibility & Missing-Class Safety**: `arch==9.0.0` requires `np.random.RandomState` (rather than `Generator`) in `res.forecast(..., method="simulation")`; `MultiHeadLGBMForecaster` handles 1-, 2-, or 3-class training windows cleanly without `LabelEncoder` crashes.
- **Dense `FORECAST_V1` Wire Representation**: `ForecastBundleV1.to_compact_prompt_str()` encodes all 14 fields into `227` local SentencePiece tokens (`<= 250` budget) vs `408` tokens for raw JSON keys.
- **Hysteresis-Stabilized Reasoning Routing**: `ReasoningEffortRouter` applies `+/-0.05` deadbands around `0.30 / 0.65 / 0.85`, eliminating boundary flapping while clamping `XHIGH -> HIGH` on `gemini-4-carbon` and allowing `XHIGH` on `gemini-4-neon`.

## 4. Test Evidence, Calibration Proofs & 12-Gate Acceptance Matrix Dry-Run

- `compileall -q src/quantum_edge_core/contracts src/quantum_edge_ml/forecasting src/quantum_edge_ml/model_router/reasoning_router.py tools/qe_harness_cli.py tools/verify_acceptance_matrix.py`: **PASS** (exit 0).
- Full offline pytest suite across Phases 0–6 (18 test files, `--hypothesis-seed=42`): **122 passed, 1 skipped** in 15.55s (0 failures).
- **Zero Future Leakage (`CausalHMMFilter`)**: modifying $X_{50:100}$ alters filtered posteriors $P(S_t | O_{1:t})$ for $t < 50$ by **`0.00e+00`**.
- **ACI-CQR Volatility-Shift Recovery (`2.2x` sigma jump)**: uncalibrated interval coverage drops to **`0.4385`** (`43.85%`), whereas `AdaptiveConformalCalibrator` restores post-burn-in online coverage to **`0.8096`** (`80.96%`, target `80%`).
- **Hysteresis Trace (`D = [0.64, 0.67, 0.63, 0.72, 0.62, 0.58, 0.92]`)**:
  - `gemini-4-carbon`: `[MEDIUM, MEDIUM, MEDIUM, HIGH, HIGH, MEDIUM, HIGH]`
  - `gemini-4-neon`: `[MEDIUM, MEDIUM, MEDIUM, HIGH, HIGH, MEDIUM, XHIGH]`
- **`tools/verify_acceptance_matrix.py --dry-run` (`12/12` Gates PASS in `/dev/shm/quantumedge/phase6_eval`)**:
  - `token_efficiency_ge_10x`: `true` (`19.65x`, `9,669.0` IECU/decision)
  - `same_frontier_model_no_downgrade`: `true` (`gemini-4-carbon`)
  - `native_usage_coverage_ge_99pct`: `true` (`1.0` coverage, `NULL` on error)
  - `cache_request_hit_s2_ge_85pct_and_token_frac_ge_50pct`: `true` (`S2 hit=0.90`, `frac=0.6614`)
  - `quota_5h_within_budget`: `true` (`7.0% <= 10.0%`)
  - `reliability_failure_lt_1pct`: `true` (`0.62% < 1.0%`, `0` 180s timeouts)
  - `latency_p95_le_18s`: `true` (`S1 p95=4,050 ms`, `overall p95=10,450 ms`)
  - `serialization_exactness_pass`: `true` (`tick_delta=0.0`, `delta_bps=0.4999 bps`)
  - `event_gate_recall_ge_90pct`: `true` (`recall=1.0`, `false_wake=0.0`)
  - `forecast_calibration_and_zero_leakage`: `true` (`leakage=0.0`, `ece=0.024`, `aci=0.802`)
  - `noninferiority_and_hold_quality_anti_hold`: `true` (`95% one-sided CI low = +1.242 bps > -2.0 bps`, `hold_quality = 0.9125`, `active_share = 0.28`)
  - `zero_risk_and_orphan_violations`: `true` (`0` risk violations, `0` orphans)

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/pytest tests/test_phase6_forecast_and_router.py tests/test_zero_duplication_guards.py -v
.venv/bin/python tools/verify_acceptance_matrix.py --dry-run
.venv/bin/python tools/qe_harness_cli.py run-cycle --transport mock --dry-run --dump-ram

# After operator-authorized 48h BingX VST soak session via OAuth proxy:
# .venv/bin/python tools/verify_acceptance_matrix.py --ledger runtime/usage_ledger.sqlite3 --replay runtime/soak_48h_replay.json
```

## 6. Readiness & Remaining Live Canary Step

- All 6 phases (`Phase 0` through `Phase 6`) of **QuantumEdge Optimization Master Roadmap v1.0** are implemented, integrated, and verified offline (`122 passed, 1 skipped`, `0` AST duplication violations).
- Next operational gate (`LIVE_VST_CANARY_FULL_OPTIMIZED_STACK_VIA_OAUTH_PROXY`): run a controlled operator-authorized BingX VST canary session over the local Antigravity OAuth proxy (`tools/agy_openai_proxy.py --ledger-db runtime/usage_ledger.sqlite3`) coupled with `MarketDataHubService`, `EventDrivenWakeupGate`, `PositionProtectionWatcher`, and `DeterministicTradingOrchestrator`, then validate live session telemetry via `tools/verify_acceptance_matrix.py`.
