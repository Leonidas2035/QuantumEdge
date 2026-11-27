# QE_OPT_HOTFIX_04_BE_SL_IDEMPOTENCY_TOKEN_EXPLOSION_AND_ROUTER_CALIBRATION_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_HOTFIX_04_BE_SL_IDEMPOTENCY_TOKEN_EXPLOSION_AND_ROUTER_CALIBRATION_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-direct-cli-sizer-session
BASE_SHA=2b83b89548035fa4076b49c96c973f37a1845fc8
TARGET_BRANCH=feat/qe-direct-cli-sizer-session

BE_SL_QUANTIZED_AND_IDEMPOTENT_ZERO_SPAM=true
AGY_THINKING_DEDUPLICATED_FROM_OUTPUT_IN_IECU=true
AGY_STATELESS_DEFAULT_NO_RESUME_O_N2=true
ROUTER_VST_SPREAD_RECALIBRATED=true
ANTI_OVER_ESCALATION_STAGE1_PROMPT_READY=true
P0_LOCAL_SP_TOKENS_AFTER_UPDATE=4458
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_FULL_SUITE_NEW_CORE=157 passed, 1 skipped in 26.34s
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

NEXT_GATE=VERIFY_LOW_QUOTA_LIVE_SESSION_AND_MERGE_PR
```

## 2. Root-Cause Fixes & Modified Files

1. **Eliminated 58x Breakeven SL Spam Loop & BingX Rate-Limit `100410`:**
   - `src/quantum_edge_core/execution/bingx_vst_adapter.py`:
     - Added shared `get_symbol_tick_size()` and `quantize_price_for_symbol()` (using `DEFAULT_INSTRUMENT_SPECS` / exchange market precision via `extract_instrument_specs()`, rounding defensively with `ROUND_CEILING` for `LONG` and `ROUND_FLOOR` for `SHORT`). Applied quantization inside `move_vst_stop_loss()` so prices strictly conform to `0.0001` (`SUIUSDT`), `0.001` (`LINKUSDT`), and `0.01` (`BNBUSDT`).
     - Hardened `fetch_vst_status()` order normalization to extract `stopPrice`/`triggerPrice` from `o.stopPrice`, `o.triggerPrice`, `o.stopLossPrice`, `o.takeProfitPrice`, nested `info.*` equivalents, and fallback `o.price`, as well as `clientOrderId` (`o.clientOrderId`, `info.clientOrderId`, `info.clientOrderID`), `positionSide`, and `rawType`.
   - `src/quantum_edge_core/execution/position_protection.py`:
     - Added `self._quantize_price()` before `existing_sl_price` comparison so unrounded float targets no longer cause repeated replace attempts.
     - Added in-RAM idempotency map `self._be_moved_positions[(symbol, side)] = round(entry_price, 6)`, plus exchange-side check for active `"qe-be-"` `STOP_MARKET` orders and `0.5 * tick` tolerance, with automatic key cleanup when a position closes.
     - Updated defaults to `breakeven_trigger_pct = 0.50` and `breakeven_offset_pct = 0.20` to cover taker exit fees + VST `STOP_MARKET` slippage.

2. **Eliminated O(N^2) `--resume` Token Explosion & Thinking Double-Count in IECU:**
   - `src/quantum_edge_ml/model_router/backends/transport.py`:
     - Added `_normalize_output_and_thinking_tokens()` in `AgyDirectCliTransport`: detects when `agy --output-format json` folds `thinking_tokens` into `output_tokens` (`raw_out >= think` and either `in + raw_out ≈ total_tokens` or `raw_out >= think + len(text)//6`) and stores visible `output_tokens = raw_out - think` while preserving separate counters (`out < think`) untouched. Prevents double-billing `thinking_tokens` at 10x in IECU (`5x visible + 5x thinking` instead of `10x`).
     - Changed default `reuse_session: bool = False` (pure stateless CLI call per prompt) so `agy --resume` no longer appends redundant `P0+P1+P2` history copies on every cycle.

3. **Recalibrated `ReasoningEffortRouter`, Stage-1 Anti-Over-Escalation & `regime_signature` Stability:**
   - `src/quantum_edge_ml/model_router/reasoning_router.py`:
     - Recalibrated VST spread liquidity risk to `min(1.0, max(0.0, spread_bps - 25.0) / 35.0)` (normal `22–26 bps` VST spreads contribute `0.00–0.03` instead of `0.85–0.95`) and smoothed position-count `exposure` (`0.0` for `<=1`, `0.35` for `2`, `0.60` for `3`) while keeping `select_level_from_score()` thresholds (`0.30/0.65/0.85 ± h`) and Carbon/Neon clamping untouched.
   - `src/quantum_edge_core/orchestrator/prompts.py`:
     - Added **Anti-Over-Escalation** rule in `SYSTEM_PROMPT_P0_CORE` instructing Stage 1 to treat flat pairs with active resting `LIMIT` entries as covered and return immediate Stage-1 `HOLD (ACTIVE_POSITION_ON_TRACK)` when all `max_open_pos` slots are covered without invalidation threat (`P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4458 >= 4096`; `P0 + P1 = 9392 <= 9500` on 4 pairs).
   - `tools/run_vst_session.py`:
     - Updated CLI defaults to `--no-reuse-session` (`reuse_session=False`), `--be-trigger-pct 0.50`, `--be-offset-pct 0.20`, and stabilized `_derive_regime_signature()` with a `>= 0.55` dominance threshold (falling back to `MIXED`) to curb spurious hourly `RESYNC` churn.

## 3. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml/model_router src/quantum_edge_ml/forecasting tools/run_vst_session.py tools/qe_harness_cli.py`: **PASS**.
- `P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4458` (`>= 4096`; `4458 + 4934 = 9392 <= 9500` in `test_phase4_prefix_cache.py`).
- Full 21-file offline suite (`--hypothesis-seed=42`, including AST `tests/test_zero_duplication_guards.py` and 5 new Phase 5/6/8 regression tests): **157 passed, 1 skipped in 26.34s** (`0` failures).
  - New BE SL tests verify exact tick quantization (`0.6702` SUI, `9.336` LINK), 10 consecutive ticks firing only once per position, re-arming after position close/reopen, and `fetch_vst_status()` nested CCXT `stopPrice`/`clientOrderId` extraction.
  - New transport/runner tests verify IECU deduplication (`20000 + 5*(694+2931) = 38125.0`) while leaving split usage untouched, plus stateless `reuse_session=False` & `0.50`/`0.20` defaults.
  - New router test confirms `25 bps` calm VST markets with 3 positions keep `complexity_score < 0.65` while genuine `60 bps` conflict escalates to `>= 0.70`.

## 4. Updated Operator Low-Quota Live VST Command

```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/run_vst_session.py \
  --live-vst \
  --transport agy-direct \
  --model gemini-4-carbon \
  --symbols BNBUSDT,LINKUSDT,SUIUSDT \
  --duration-minutes 60.0 \
  --cycle-interval-sec 180.0 \
  --be-trigger-pct 0.50 \
  --be-offset-pct 0.20 \
  --max-margin-vst 1000 \
  --max-leverage 50 \
  --max-open-positions 3 \
  --max-notional-vst 50000 \
  --close-on-exit \
  --report-out reports/tasks/QE_OPT_LIVE_1H_LOW_QUOTA_SESSION_REPORT.md \
  --sync-gdrive
```

## 5. Git & Artifacts

- Included untracked session artifact `reports/tasks/QE_LIVE_2H_VST_BNB_LINK_SUI_DIRECT_AGY_V1.md` and synced this hotfix report to `/home/layman/mnt/gdrive-reports/`.
- Committed and pushed exclusively to `origin/feat/qe-direct-cli-sizer-session`.

## 6. Compliance & Safety Checklist

- `GIT_PUSH_AUTHORIZED=true` only to `feat/qe-direct-cli-sizer-session` (never `main`, never `--force`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` untouched; AST Zero-Duplication Guards **100% PASS**.
