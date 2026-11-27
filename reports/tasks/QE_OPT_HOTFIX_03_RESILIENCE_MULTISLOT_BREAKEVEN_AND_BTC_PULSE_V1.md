# QE_OPT_HOTFIX_03_RESILIENCE_MULTISLOT_BREAKEVEN_AND_BTC_PULSE_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_HOTFIX_03_RESILIENCE_MULTISLOT_BREAKEVEN_AND_BTC_PULSE_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-direct-cli-sizer-session
BASE_SHA=e47b216369edec019cce8bb2a7c998fdcaab00e6
TARGET_BRANCH=feat/qe-direct-cli-sizer-session

AGY_TRANSIENT_RETRY_AND_BACKOFF_READY=true
AGY_QUOTA_FAST_FAIL_READY=true
AGY_SESSION_AUTO_RESET_ON_MAX_TURNS=true
MOVE_VST_STOP_LOSS_CREATE_BEFORE_CANCEL=true
BREAKEVEN_SL_LONG_SHORT_READY=true
MULTISLOT_MANDATE_READY=true
BTC_LEADER_READONLY_PULSE_READY=true
SAFE_CLOSE_ON_EXIT_CRASH_GUARD_READY=true
LIVE_CONSOLE_THESIS_DI_EXEC_READY=true
P0_LOCAL_SP_TOKENS_AFTER_UPDATE=4555

COMPILEALL_STATUS=PASS
ZERO_DUPLICATION_GUARDS_PASS=true
PYTEST_FULL_SUITE=149 passed, 2 skipped in 26.00s
TOTAL_FAILED=0

EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
LIVE_READS=0
LIVE_WRITES=0
PROVIDER_GETS=0
PROVIDER_MUTATIONS=0
REAL_TRADING=false

NEXT_GATE=OPERATOR_FOREGROUND_1H_LIVE_VST_SESSION
```

## 2. Root-Cause Fixes & Modified Files

- `src/quantum_edge_ml/model_router/backends/transport.py` (modified):
  - Extended `AgyDirectCliTransport` with `max_transient_retries=2`, `retry_backoff_sec=(2.0, 5.0)`, `max_resume_turns=4`, `reset_session()`, `_is_quota_error()`, and `_is_transient_error()`.
  - On transient CLI/network/timeout failure: records the failed attempt (`TIMEOUT` or `ERROR` with `NULL` native token counts) to `UsageLedger`, clears chained `--resume` state (`reset_session()`), awaits exponential backoff, and retries cleanly without `--resume` while linking `retry_of=first_record_id`.
  - On quota exhaustion (`quota`, `exhausted`, `usage limit`, `resource_exhausted`, `quota_reached`): logs `RequestStatus.QUOTA_REACHED` with zero retries and raises immediately.
  - Automatically resets `--resume` session chaining after `max_resume_turns` successful turns.
- `src/quantum_edge_core/execution/bingx_vst_adapter.py` (modified):
  - Fixed `fetch_vst_status()` `rawType` extraction (`info.get("type") or info.get("rawType")`) so `STOP_MARKET`/`TAKE_PROFIT_MARKET` orders are normalized reliably across both CCXT live payloads and sandbox mocks.
  - Added `move_vst_stop_loss()` enforcing `is_vst_sandbox_client()`, creating the replacement reduce-only `STOP_MARKET` order (`clientOrderId` prefix `"qe-be-"`, `positionSide`, `reduceOnly=True`, `stopPrice`) first, and only cancelling `old_order_id` after receiving a confirmed `new_order_id` (logging warning without throwing if cancelling the replaced order fails).
- `src/quantum_edge_core/execution/position_protection.py` (modified):
  - Added `enable_breakeven_sl=True`, `breakeven_trigger_pct=0.40`, `breakeven_offset_pct=0.12` to `PositionProtectionWatcher.check_once()`.
  - Moves SL to entry `+0.12%` (`LONG`) or `-0.12%` (`SHORT`) once favorable unrealized move reaches `>= 0.40%`, strictly improving existing SL protection (idempotent / never moving SL backward) and supporting `DRY_WOULD_MOVE_BE_SL` in `dry_run=True`.
- `src/quantum_edge_core/orchestrator/prompts.py` (modified):
  - Added Multi-Slot Mandate (`max_open_pos >= 2` anti-defensive-`HOLD`), `BTC_LEADER` read-only macro anchor rule, and explicit `di_diff > 0` / `di_diff < 0` sign semantics in `SYSTEM_PROMPT_P0_CORE` (`P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4555 >= 4096`, `P0 + P1 = 9489 <= 9500`).
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py` & `prefix_context_assembler.py` (modified):
  - Added `PreFetchedBundle.market_leader`, shared `format_btc_leader_line()`, `_fetch_btc_leader_pulse()` (lightweight fail-soft read-only `BTCUSDT` 15m/1h fetch when `BTCUSDT` is outside active pairs), and injected `BTC_LEADER|...` into Stage-1 & Stage-2 `P2` across both assemblers without adding `BTCUSDT` to tradable symbols.
  - Added `di={p['di_diff']}` to `format_tactical_indicators_line()`.
- `tools/run_vst_session.py` (modified):
  - Added `--be-trigger-pct`, `--be-offset-pct`, `--no-breakeven-sl`, `--no-btc-leader`, and `--close-on-crash` CLI flags.
  - Added per-cycle `try/except` resilience (`cycle_errors_count`, clean stop on quota errors, `RESYNC -> transport.reset_session()`), safe `--close-on-exit` (cleans up on normal expiry / `SIGINT` / `SIGTERM`, leaves positions + exchange SL/TP protected on unhandled crash unless `--close-on-crash`), and enhanced live console (`BTC_LDR`, `di=` in `PAIR`, full `THESIS`, `pre_pos/post_pos`, `pre_ord/post_ord`, and watcher protection actions in `EXEC`).
- `tests/test_phase5_wakeup_and_protection.py`, `tests/test_phase8_direct_cli_sizer_and_runner.py`, `tests/test_phase9_tf_1d_indicators_and_console.py` (modified):
  - Added offline regression tests covering transient crash `retry_of` + backoff, quota fast-fail, `max_resume_turns` reset, `move_vst_stop_loss` create-before-cancel invariants, LONG/SHORT/never-backward breakeven SL migration, `BTC_LEADER` scope exclusion + prompt token budgets, and `run_vst_session` cycle error survival / quota clean stop / crash-safe exit preservation.

## 3. Architectural Safety Guarantees

- **Zero Unprotected Gap During SL Replacement:** `move_vst_stop_loss()` refuses to cancel `old_order_id` if the replacement `STOP_MARKET` order fails or omits an exchange ID, and strictly compares target breakeven stop vs existing stop so trailing stops are never loosened backward.
- **No Crash-Induced Forced Liquidation:** Unhandled `BaseException` exits bypass `--close-on-exit` liquidation unless `--close-on-crash` is explicitly passed, keeping exchange-side `STOP_MARKET`/`TAKE_PROFIT_MARKET` brackets intact.
- **BTC Leader Context Without Universe Contamination:** `PreFetchCoordinator` excludes `BTCUSDT` from `contexts_by_symbol` and `allowed_symbols` whenever `BNBUSDT,LINKUSDT,SUIUSDT` is traded; `LeanRiskGuard` and `DeterministicTradingOrchestrator` remain strictly scoped to the configured universe.

## 4. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml tools`: **PASS** (exit 0).
- `P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4555` (`>= 4096`; `P0 + P1 = 9489 <= 9500` on 4-pair fixture).
- 22-file offline regression suite (`--hypothesis-seed=42`): **149 passed, 2 skipped in 26.00s** (`0` failures; 2 skips are opt-in live-exchange suites when `RUN_LIVE_BINGX_VST_TESTS` is unset).

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
  --be-trigger-pct 0.40 \
  --be-offset-pct 0.12 \
  --max-margin-vst 1000 \
  --max-leverage 50 \
  --max-open-positions 3 \
  --max-notional-vst 50000 \
  --close-on-exit \
  --report-out reports/tasks/QE_OPT_LIVE_1H_SESSION_REPORT.md \
  --sync-gdrive
```

## 6. Compliance & Safety Checklist

- `GIT_PUSH_AUTHORIZED=true` exclusively to `feat/qe-direct-cli-sizer-session` (never `main`, never `--force`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` untouched; AST Zero-Duplication Guards **100% PASS**.
