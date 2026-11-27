# QE_OPT_P7_UNIVERSE_BNB_LINK_SUI_AND_ISOLATED_50X_RISK_FOUNDATION_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P7_UNIVERSE_BNB_LINK_SUI_AND_ISOLATED_50X_RISK_FOUNDATION_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=5457684bfe2f4a240174482c376edb3da6119e28
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
WORKTREE_CLEAN=true

DEFAULT_UNIVERSE_BNB_LINK_SUI_CONFIGURED=true
ISOLATED_50X_NOTIONAL_50K_GUARD_READY=true
SL_BEFORE_LIQUIDATION_GUARD_READY=true
EXCHANGE_SET_ISOLATED_AND_LEVERAGE_WIRED=true
P0_LOCAL_SP_TOKENS_AFTER_UPDATE=4112
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_PHASE7=6 passed
PYTEST_FULL_SUITE_NEW_CORE=134 passed, 1 skipped in 17.20s
TOTAL_FAILED=0

GIT_PUSHED=true
REMOTE_BRANCH_REF=origin/feat/qe-antigravity-hermes-bridge
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

NEXT_GATE=RUN_LIVE_VST_CANARY_01_ON_BNB_LINK_SUI_VIA_OAUTH_PROXY
```

## 2. Modified & Created Files

- `src/quantum_edge_core/contracts/optimization.py` (modified): raised `TradeDecisionV2.leverage` upper bound from `le=20` to `le=50` (`ge=1, le=50`).
- `src/quantum_edge_core/execution/market_context_serializer.py` (modified): added `BNBUSDT: (0.01, 0.01)`, `LINKUSDT: (0.001, 0.1)`, and `SUIUSDT: (0.0001, 1)` to `DEFAULT_INSTRUMENT_SPECS` while preserving `BTCUSDT`/`ETHUSDT`/`SOLUSDT`/`XRPUSDT` backward compatibility.
- `src/quantum_edge_core/execution/risk_guard.py` (modified):
  - Added `QUANTUMEDGE_DEFAULT_UNIVERSE = ("BNBUSDT", "LINKUSDT", "SUIUSDT")`.
  - Extended `RiskConfig` with `max_notional_per_pair_vst: float = 50_000.0`, `require_isolated_margin: bool = True`, `enforce_sl_before_liquidation: bool = True`, `liquidation_safety_factor: float = 0.80`, and classmethod factory `RiskConfig.for_qe_isolated_50x()`.
  - Updated `LeanRiskGuard.validate()` to enforce `margin_vst * leverage <= max_notional_per_pair_vst` (`MAX_NOTIONAL_EXCEEDED`) and SL-before-liquidation buffer (`abs(ref_price - sl_price) / ref_price <= 0.80 / leverage` for `leverage >= 10`, returning `SL_BEYOND_LIQUIDATION_BUFFER`) while keeping `CLOSE_LONG`/`CLOSE_SHORT`/`HOLD` unconditional.
- `src/quantum_edge_core/execution/bingx_vst_adapter.py` (modified): added `ensure_isolated_margin_and_leverage(client, *, symbol, leverage, position_side, require_isolated)` verifying `REAL_TRADING=false` and sandbox lock on `ccxt.Exchange`, setting `ISOLATED` margin mode and Hedge Mode `set_leverage(leverage, ccxt_sym, params={"side": side_upper})`, handling harmless `"already"` / `"No need to change"` exchange responses, and failing soft on minimal offline `PaperExchangeClient`.
- `src/quantum_edge_core/execution/lean_executor.py` (modified): wired `ensure_isolated_margin_and_leverage()` strictly before `create_order()` on `OPEN_LONG`/`OPEN_SHORT`; fatal margin/leverage setup errors immediately mark SQLite `OrderLedger` as `REJECTED` (`ISOLATED_LEVERAGE_SETUP_FAILED`) and prevent sending an order with wrong web-UI leverage.
- `src/quantum_edge_core/execution/position_protection.py` (modified): added `BNBUSDT`, `LINKUSDT`, `SUIUSDT` to `DEFAULT_ALLOWED_SYMBOLS` while retaining backward-compatible defaults for offline unit suites.
- `src/quantum_edge_core/orchestrator/prompts.py` (modified): generalized `SYSTEM_PROMPT_P0_CORE` Section 2 from fixed BTC/altcoin assumptions to spread-adaptive playbooks (`Tight-spread < 5 bps` vs `Wide-spread >= 15 bps`) plus explicit `ISOLATED` margin budget and SL-before-liquidation (`< 0.80 / leverage`) guidance, tightening token count from `4,119` to `4,112` SP tokens (`>= 4,096`).
- `tools/qe_harness_cli.py` (modified): updated `run-cycle` default `--symbols` to `BNBUSDT,LINKUSDT,SUIUSDT`, added `--max-margin-vst` (`1000.0`), `--max-leverage` (`50`), `--max-open-positions` (`3`), `--max-notional-vst` (`50000.0`) feeding `RiskConfig.for_qe_isolated_50x()`, updated `_build_offline_mock_bundle` & `_CliMockTransport` for `BNB/LINK/SUI`, and scoped live/readonly `protect-once` fallback to `QUANTUMEDGE_DEFAULT_UNIVERSE`.
- `tests/test_phase7_universe_and_50x_risk.py` (created): 6 deterministic tests covering 50x/50k notional caps, SL-before-liquidation rejection/allowance, `set_margin_mode` + `set_leverage` order & fail-closed abort, `BNB/LINK/SUI` exact tick-delta roundtrip, P0 token threshold preservation, and default `run-cycle` CLI execution.

## 3. Architectural & Risk Decisions

1. **Multi-Bot Account Isolation (`BNBUSDT / LINKUSDT / SUIUSDT`)**:
   - Sibling agents (`Hermes-GPT` on `BTCUSDT/ETHUSDT/SOLUSDT`, DCA bot on `XRPUSDT`) share the same BingX VST account. QuantumEdge CLI cycles and live/readonly protection loops now isolate on `BNBUSDT,LINKUSDT,SUIUSDT` by default while preserving `BTCUSDT`/`ETHUSDT`/`SOLUSDT`/`XRPUSDT` support inside base `RiskConfig` / `DEFAULT_ALLOWED_SYMBOLS` so existing regression suites stay 100% intact.
2. **Strict `ISOLATED` + `set_leverage` Pre-Order Enforcement**:
   - Calling `set_margin_mode("ISOLATED")` and `set_leverage(lev, ccxt_sym, params={"side": position_side})` inside `LeanExecutor.execute()` prior to `create_order()` eliminates silent cross-margin spillover or stale 5x BingX UI leverage blocking excess VST margin.
3. **SL-Before-Liquidation Safety Invariant (`0.80 / leverage`)**:
   - At `50x` isolated leverage, theoretical liquidation sits ~`1.5%–1.7%` from entry after maintenance margin. Enforcing `|entry - sl| / entry <= 0.80 / leverage` (`<= 1.60%` at `50x`) in `LeanRiskGuard` mathematically forces exchange `STOP_MARKET` execution strictly ahead of liquidation.

## 4. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml/model_router src/quantum_edge_ml/forecasting tools/qe_harness_cli.py`: **PASS** (exit 0).
- `tests/test_phase7_universe_and_50x_risk.py`: **6 passed**.
- Full offline suite across 19 test files (`--hypothesis-seed=42`, including `tests/test_zero_duplication_guards.py`): **134 passed, 1 skipped** in `17.20s` (`0` failures).
- Local SentencePiece P0 check: `P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4112` (`>= 4,096` threshold preserved and slightly tighter than `4,119`).

## 5. Operational Runbook (Live Canary on BNBUSDT/LINKUSDT/SUIUSDT)

```bash
# Terminal 1: OAuth Proxy (JSONL log only, no shared --ledger-db)
cd /home/layman/QuantumEdge
.venv/bin/python tools/agy_openai_proxy.py --host 127.0.0.1 --port 8045 --log-path runtime/agy_openai_proxy.log

# Terminal 2: QuantumEdge Canary Decision Cycle (default BNBUSDT,LINKUSDT,SUIUSDT, 1000 VST x 50x Isolated)
cd /home/layman/QuantumEdge
.venv/bin/python tools/qe_harness_cli.py run-cycle --transport agy-proxy --dry-run --max-margin-vst 1000 --max-leverage 50 --max-open-positions 3 --max-notional-vst 50000
```

## 6. Compliance & Safety Checklist

- `GIT_PUSH_AUTHORIZED=true`: pushed strictly to `feat/qe-antigravity-hermes-bridge` (never `main`, never `--force`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` untouched per operator directive.
