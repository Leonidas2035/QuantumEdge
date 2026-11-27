# QE_OPT_HOTFIX_02_AUDIT_FIXES_MULTI_AGENT_ISOLATION_AND_STAGE2_TUNING_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_HOTFIX_02_AUDIT_FIXES_MULTI_AGENT_ISOLATION_AND_STAGE2_TUNING_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=562b94fe03a5583dd3fe3fc405c3d26f73496cf1
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
WORKTREE_CLEAN=true

VST_STATUS_SCOPED_IN_ORCHESTRATOR=true
DYNAMIC_SYMBOLS_UNLOCKED=true
AGY_ALIAS_NORMALIZED_AND_TIMEOUTS_CONFIGURABLE=true
STAGE2_OHLCV_DEDUPLICATED=true
P2_STAGE2_LOCAL_SP_TOKENS_AFTER_DEDUP=3750
INTRA_HOUR_THESIS_DELTA_IN_P2=true
PER_SYMBOL_ORPHAN_CLEANUP_VERIFIED=true
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_FULL_SUITE_NEW_CORE=128 passed, 1 skipped in 17.46s
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

NEXT_GATE=RETRY_QE_OPT_CANARY_01_VIA_OAUTH_PROXY
```

## 2. Modified Files

- `src/quantum_edge_core/contracts/optimization.py`:
  - Replaced hardcoded 4-symbol `Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]` on `ScreeningDecisionV1.candidate_symbol` (`str | None = None`) and `TradeDecisionV2.symbol` (`str = Field(..., min_length=4, max_length=20)`).
  - Added `_normalize_trading_symbol()` via `@field_validator` normalizing `.strip().upper()` and validating 4..20 alphanumeric characters while preserving session-level enforcement in `RiskConfig.allowed_symbols`, `DeterministicTradingOrchestrator`, and `LeanRiskGuard`.
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py`:
  - Added `scope_vst_status_to_symbols(vst_status, allowed_symbols)` returning a copy of `vst_status` with `open_positions`, `open_orders`, `open_positions_count`, and `open_orders_count` filtered strictly to `allowed_symbols`, recording `ignored_foreign_positions_count` and `ignored_foreign_orders_count`.
  - Applied `scope_vst_status_to_symbols()` in `PreFetchCoordinator.fetch_all()` and defensively at the top of `DeterministicTradingOrchestrator.run_decision_cycle()` so foreign manual positions (e.g., `BNBUSDT`) or sibling bots (`Hermes-GPT`) never pollute LLM prompts, `ReasoningEffortRouter`, or `LeanRiskGuard`.
  - Added `stage1_timeout_sec: float = 90.0` and `stage2_timeout_sec: float = 150.0` to `DeterministicTradingOrchestrator.__init__()`.
  - Added out-of-scope guards in `run_decision_cycle()` returning `validator_passed=False, validator_reason=f"SYMBOL_OUT_OF_SCOPE:{cand_sym}"` safely without `KeyError`.
  - Added shared `format_forecast_for_prompt()` and `serialize_stage2_candidate()` eliminating Stage-2 `DELTA_BPS_V1` / `OHLCV_TICK_V1` duplication in `StatelessStagePromptAssembler` and `PrefixContextAssembler`.
- `src/quantum_edge_core/orchestrator/prefix_context_assembler.py`:
  - Added `ThesisMemoryStore.max_rowid()` and `render_intra_hour_delta_block(since_rowid=...)`, plus `CheckpointState.thesis_watermark` in `SnapshotStore`.
  - `PrefixContextAssembler.assemble_stage1()` and `assemble_stage2()` append `INTRA_HOUR_THESIS_DELTA|...` to `P2` whenever theses are inserted/updated after the current `P1` checkpoint, preserving 100% byte stability of `P0 + P1` for Gemini 4 Implicit Caching while eliminating intra-hour position amnesia.
  - Updated `assemble_stage2()` (`include_images=False`) to emit structural profiles `PROF_4h`/`PROF_1h` (since 30-bar `delta_bps` is already in `P1`), `PROF_*` + exact 25-bar `OHLCV_TICK_V1` on `15m/5m/1m` (no duplicate `DELTA_BPS_V1`), and full `LIQMAP` / `L2_TOP15`.
  - Rendered `ForecastBundleV1` (or matching dicts) via dense `to_compact_prompt_str()` (`FORECAST_V1|...`).
- `src/quantum_edge_ml/model_router/backends/transport.py`:
  - Added `normalize_agy_model_alias(model, level)` stripping any existing `-low/-medium/-high/-xhigh` suffix, clamping via `clamp_thinking_level()`, and returning canonical `agy_alias`.
  - Added `record_to_ledger: bool = True` to `AgyProxyTransport.__init__()` for double-ledger protection.
- `src/quantum_edge_core/execution/position_protection.py`:
  - Added `order_id_prefix: str | None = "qe-"` and `strict_prefix: bool = False` with `_is_managed_order()`, always ignoring orders carrying non-empty foreign prefixes (e.g. `hermes-...`, `xrp-dca-...`) even on allowed symbols.
  - Replaced global `if len(positions) == 0` orphan check with per-symbol `active_pos_symbols` filtering, cancelling only protective orphans on symbols with no open position (`cancel_vst_open_orders(client, sym)` when isolated, or targeted `cancel_order` if foreign unmanaged orders share that symbol).
- `tools/qe_harness_cli.py`:
  - Exposed `--stage1-timeout` (default `90.0`) and `--stage2-timeout` (default `150.0`) on `run-cycle`.
  - Documented single-writer `UsageLedger` rule: `AgyProxyTransport` records full `UsageRecordV1` telemetry directly into `--ledger-db`, so `tools/agy_openai_proxy.py` must be launched without sharing `--ledger-db` (JSONL log only).
- `tests/test_phase1_usage_ledger_and_transport.py`, `tests/test_phase3_orchestrator.py`, `tests/test_phase4_prefix_cache.py`, `tests/test_phase5_wakeup_and_protection.py`:
  - Added regression tests for all 6 audit findings.

## 3. Architectural & Audit Fix Details

1. **Multi-Agent Shared Account Isolation (`scope_vst_status_to_symbols` + Per-Symbol Orphan Cleanup + `qe-` Prefix)**:
   - Shared BingX VST accounts hold foreign manual trades (e.g. `BNBUSDT`) and sibling bot orders (`hermes-...`, `xrp-dca-...`). Scoping occurs at ingestion (`PreFetchCoordinator`), orchestration entry (`DeterministicTradingOrchestrator`), risk verification (`LeanRiskGuard`), and deterministic cleanup (`PositionProtectionWatcher`).
   - `PositionProtectionWatcher` now checks `sym not in active_pos_symbols` per symbol so an orphan SL on `ETHUSDT` is cancelled immediately even while `BTCUSDT LONG` remains open and guarded.
2. **Stage-2 Deduplication & Intra-Hour Memory Continuity**:
   - Eliminated redundant 25-bar `DELTA_BPS_V1` encoding alongside `OHLCV_TICK_V1` on `15m/5m/1m` in Stage 2 (`include_images=False`), reducing Stage-2 candidate token payload from `5,296` to `3,611` SP tokens (`3,750` full `P2_STAGE2`), comfortably exceeding the `>= 2,050` implicit cache minimum while keeping lossless tick resolution for SL/TP placement.
   - `ThesisMemoryStore` rowid watermarks guarantee intra-hour opens/closes appear in `P2` via `INTRA_HOUR_THESIS_DELTA|...` with zero mutation to `P0 + P1` `prefix_hash`.
3. **Transport Alias & Ledger Hygiene**:
   - `normalize_agy_model_alias("gemini-4-carbon-medium", ThinkingLevel.LOW)` -> `"gemini-4-carbon-low"`; `"gemini-4-carbon"` + `XHIGH` -> `"gemini-4-carbon-high"`; `"gemini-4-neon"` + `XHIGH` -> `"gemini-4-neon-xhigh"`.
   - Note: Per operator directive, legacy Telegram `BOT_TOKEN` warning in `telegram_notifier.py` was left untouched and ignored.

## 4. Test Evidence & Token Measurements

- `compileall -q src/quantum_edge_core src/quantum_edge_ml/model_router src/quantum_edge_ml/forecasting tools/qe_harness_cli.py`: **PASS** (exit 0).
- Full offline pytest suite across 18 test files (`--hypothesis-seed=42`): **128 passed, 1 skipped** in `17.46s` (`0` failures; `tests/test_zero_duplication_guards.py` PASS).
- Measured local SentencePiece token counts (`PrefixContextAssembler(include_images=False)` on `runtime/step1_enriched_ctx_btcusdt.json`):
  - `P0_TOKENS = 4119` (`>= 4,096`)
  - `P1_TOKENS = 4527` (`P0 + P1 = 8,646`)
  - `P2_STAGE1_TOKENS = 1157`
  - `P2_STAGE2_LOCAL_SP_TOKENS_AFTER_DEDUP = 3750` (`>= 2,050`)

## 5. Operational Runbook

```bash
# Terminal 1: Local OAuth Proxy (JSONL log only — NO shared --ledger-db to prevent double writes)
cd /home/layman/QuantumEdge
.venv/bin/python tools/agy_openai_proxy.py --host 127.0.0.1 --port 8045 --log-path runtime/agy_openai_proxy.log

# Terminal 2: 2-Stage Decision Cycle (AgyProxyTransport writes complete UsageRecordV1 to usage_ledger.sqlite3)
cd /home/layman/QuantumEdge
.venv/bin/python tools/qe_harness_cli.py run-cycle --transport agy-proxy --dry-run --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT --stage1-timeout 90 --stage2-timeout 150
```

## 6. Compliance & Safety Checklist

- `GIT_PUSH_AUTHORIZED=true`: pushed ONLY `feat/qe-antigravity-hermes-bridge` (no `main`, no `--force`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` not modified (operator directive honored).
