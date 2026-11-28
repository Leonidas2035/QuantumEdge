# QE_D2_04C_OVERNIGHT_FIXES_LIMIT_TTL_SINGLE_ENTRY_WATCHER_HEARTBEAT_AND_SCHEMA_V1

## 1. Key-Value Summary

```text
TASK=QE_D2_04C_OVERNIGHT_FIXES_LIMIT_TTL_SINGLE_ENTRY_WATCHER_HEARTBEAT_AND_SCHEMA_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-d2-04-market-data-infra
BASE_SHA=4a494f1f57a1a4c1bf09417e215bef7ef88eaffb
TARGET_BRANCH=feat/qe-d2-04-market-data-infra

WATCHER_GRACE_AND_HEARTBEAT_FIXED=true
STALE_LIMIT_TTL_AND_NO_STACKING_READY=true
SCHEMA_AUTO_TRUNCATE_AND_BINGX_RETRY_READY=true
EQUITY_BREAKDOWN_TRUE_PNL_READY=true
HMM_1H_AND_STABLE_PREFIX_SIGNATURE_READY=true
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_QE04_SUITE=15 passed
PYTEST_FULL_SUITE_NEW_CORE=172 passed, 1 skipped in 37.85s
TOTAL_FAILED=0

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

NEXT_GATE=MERGE_QE04_TO_MAIN_AND_START_QE05_COINGECKO
```

## 2. Root-Cause Fixes Implemented

1. **Eliminated False `EMERGENCY_CLOSE` on Newly Filled `LIMIT` Positions (`position_protection.py`, `run_vst_session.py`):**
   - Added `new_position_sl_grace_sec = 20.0` (`_position_first_seen_ts` keyed by `(symbol, side)` with real epoch heartbeat check) so newly filled `LIMIT` positions are never falsely market-closed while exchange-side `STOP_MARKET` attaches, while `near_liq` still triggers immediate emergency exit.
   - Added `watcher.touch_feed_heartbeat()` on every successful `check_once()` REST poll inside `run_vst_session.py`.

2. **Prevented Entry `LIMIT` Order Stacking & 4.5h Stale `LIMIT` Hanging (`bingx_vst_adapter.py`, `position_protection.py`, `deterministic_orchestrator.py`):**
   - Added `cancel_vst_order_by_id()` (sandbox-verified, rate-limited, fail-soft, cache-invalidating).
   - Added `limit_order_ttl_sec = 2700.0` (45 min, CLI `--limit-order-ttl-min 45.0`) to `PositionProtectionWatcher.check_once()` to automatically expire stale unfilled entry `LIMIT` orders (`EXPIRED_STALE_LIMIT:<SYM>:<OID>`).
   - Added replace-on-update guard in `DeterministicTradingOrchestrator.run_decision_cycle()`: cancels any existing resting non-`reduceOnly` `LIMIT` order on the target symbol before submitting a new entry `LIMIT`.

3. **Schema Auto-Truncation & BingX `100421`/Network Retry (`optimization.py`, `bingx_vst_adapter.py`):**
   - Increased `max_length` to `400` with `mode="before"` truncation (`_truncate_str_400`) on `ScreeningDecisionV1` (`market_regime_summary`, `candidate_rationale`) and `TradeDecisionV2` (`regime_read`, `primary_edge`, `counter_thesis`).
   - Extended `BingXRestRateLimiter.call_with_rate_limit()` (`_is_bingx_transient_error`) to retry up to 2 times with jittered backoff on `100421` timestamp mismatch, `RemoteDisconnected`, `ConnectionError`, `NetworkError`, and `ReadTimeout`.

4. **Accurate Equity Breakdown (`run_vst_session.py`):**
   - Added `_compute_equity_breakdown()` separating `free_vst`, `open_orders_est_margin_vst`, and effective `total_vst`, reporting `END_EQUITY_TOTAL_VST`, `END_BALANCE_FREE_VST`, `OPEN_ORDERS_LOCKED_MARGIN_VST`, and `EST_TRUE_PNL_VST` in the forensic summary.

5. **1h `CausalHMMFilter` & Stable `1d/4h` `prefix_hash` Signature (`deterministic_orchestrator.py`, `run_vst_session.py`):**
   - `LocalForecastProvider` now fits/caches `CausalHMMFilter` on `1h` bars (`>= 60` bars, ~4 days structure) while keeping `MultiHeadLGBMForecaster` + `EGARCH` on `15m`.
   - `_derive_regime_signature()` now keys off `1d/4h` `di_diff` signs (`UP`/`DOWN`/`MIXED`), eliminating noisy 15m HMM `RESYNC` churn.

## 3. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml tools/run_vst_session.py tools/qe_briefing.py tools/qe_harness_cli.py`: **PASS**.
- Full 22-file regression suite (`--hypothesis-seed=42`, including 5 new QE-04C tests & `test_zero_duplication_guards.py`): **172 passed, 1 skipped in 37.85s** (`0` failures).
