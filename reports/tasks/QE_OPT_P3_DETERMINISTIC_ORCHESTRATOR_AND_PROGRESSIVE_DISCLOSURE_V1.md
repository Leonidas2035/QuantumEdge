# QE_OPT_P3_DETERMINISTIC_ORCHESTRATOR_AND_PROGRESSIVE_DISCLOSURE_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P3_DETERMINISTIC_ORCHESTRATOR_AND_PROGRESSIVE_DISCLOSURE_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=be44db85b36fa89864d5f19eb4350045557c547b
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
CAUSAL_TRADE_DECISION_V2_READY=true
TRADE_OR_JUSTIFY_P0_STATIC_READY=true
PREFETCH_RAM_ONLY_ZERO_DISK_VERIFIED=true
STAGE1_HOLD_ONE_CALL_VERIFIED=true
STAGE2_DRILLDOWN_HARNESS_BRIDGE_VERIFIED=true
ALT_SPREAD_PROTOCOL_VERIFIED=true
REPLAY_C1_TOTAL_INPUT_CHARS=30390
REPLAY_C1_REDUCTION_VS_QE_D2_10=27.77
REPLAY_C4_TOTAL_INPUT_CHARS=12824
CLI_RUN_CYCLE_MOCK_DRY_RUN=PASS
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE3=8 passed
PYTEST_FULL_SUITE_NEW_CORE=96 passed, 1 skipped
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
NEXT_GATE=QE_OPT_P4_PREFIX_CACHE_ASSEMBLER_AND_THESIS_MEMORY
```

## 2. Modified & Created Files

- `src/quantum_edge_core/contracts/optimization.py` (modified): added `HoldBlockerCode`, `ScreeningDecisionV1` (Stage 1), and `TradeDecisionV2` (Stage 2 with strict causal field ordering `regime_read -> primary_edge -> counter_thesis -> invalidation_price -> action`, Trade-or-Justify validation, LONG/SHORT/LIMIT geometry checks, and `to_lean_decision_dict()` mapping `REDUCE` -> `CLOSE_LONG`/`CLOSE_SHORT` with safe `margin_vst=0.0` / `leverage=1` defaults for `contracts/lean.py::TradeDecision`).
- `src/quantum_edge_core/contracts/lean.py` & `src/quantum_edge_core/contracts/__init__.py` (modified): non-invasive re-exports of `HoldBlockerCode`, `ScreeningDecisionV1`, and `TradeDecisionV2`.
- `src/quantum_edge_core/orchestrator/prompts.py` (created): byte-stable `SYSTEM_PROMPT_P0_CORE` (Trade-or-Justify mandate, BingX VST spread protocol for BTC and 20–27 bps altcoins, 4-step anti-overthinking discipline, encoding reference), `CANONICAL_FEW_SHOT_EXAMPLES` (4 calibration examples), and `build_p0_static_prefix()`.
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py` (created): `PreFetchedBundle`, `PreFetchCoordinator` (parallel `asyncio.gather` + `asyncio.to_thread` in RAM, 0 SSD context dumps), `StatelessStagePromptAssembler` (text-only OAuth proxy mode with exact `OHLCV_TICK_V1` 15m/5m/1m tails + multimodal WebP mode + Phase 4 `observe_cache_result`/`upsert_thesis` hooks), `parse_structured_model_json()`, and `DeterministicTradingOrchestrator`.
- `src/quantum_edge_core/orchestrator/__init__.py` (created): package exports.
- `tools/qe_harness_cli.py` (modified): added `run-cycle` subcommand (`--dry-run` default / `--live-vst`, `--transport mock|agy-proxy|gemini-direct`, `--proxy-url`, `--model`, `--symbols`, `--trigger`, `--include-images/--no-images`, `--ledger-db`, `--order-ledger-db`, `--journal-path`, `--fixture`, `--dump-ram`).
- `tests/test_phase3_orchestrator.py` (created): 8 offline unit, CLI, and `QE_D2_10` paired replay tests.

## 3. Architectural Decisions

- **Eliminating $O(N^2)$ ReAct Loops**: `PreFetchCoordinator` gathers VST account status and all 4 enriched `FullMarketContext` objects concurrently in RAM (`PreFetchedBundle`) before any LLM call, eliminating iterative `read_file` tool loops.
- **Native Antigravity OAuth Proxy Compatibility**: `StatelessStagePromptAssembler(include_images=False)` is the default for `--transport agy-proxy`, combining `multiscale` structural profiles, full `LIQMAP` & `L2_TOP15` CSVs, and exact lossless 25-bar `OHLCV_TICK_V1` tails on `15m`/`5m`/`1m` (for precision SL/TP anchoring without rendering unused WebP bytes), while `parse_structured_model_json()` strips markdown code fences if emitted by chat proxies.
- **Anti-HOLD & Altcoin Spread Protocol**: No bureaucratic prompt interlocks; `HOLD` requires an explicit `HoldBlockerCode != NONE`. `_post_validate_trade()` permits passive `LIMIT` orders on wide-spread altcoins (`spread >= 15 bps`) without artificial target floors, while gating `MARKET` orders on wide-spread altcoins at `tp_bps >= max(70.0, 2.5 * spread_bps)`. All hard risk limits execute exclusively via the existing `HarnessBridge -> LeanRiskGuard -> LeanExecutor -> OrderLedger` pipeline.
- **Phase 4 Forward Compatibility**: `DeterministicTradingOrchestrator` delegates cache-hit tracking and thesis upserts through `_notify_cache_result()` and `_notify_thesis_upsert()`, supporting both Phase 3 `StatelessStagePromptAssembler` and Phase 4 `PrefixContextAssembler` (`snapshot_store` / `thesis_store`).

## 4. Test Evidence & Paired Replay vs `QE_D2_10`

- `compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/execution src/quantum_edge_core/orchestrator tools/qe_harness_cli.py`: **PASS** (exit 0).
- Full offline pytest suite (15 test files, `--hypothesis-seed=42`): **96 passed, 1 skipped** (`test_bingx_vst.py` skipped offline; 0 failures).
- `tests/test_zero_duplication_guards.py`: **5 passed** (confirmed zero duplicate `Executor`/`RiskGuard`/`ccxt.bingx`).

### Paired Replay Comparison vs `QE_D2_10` Telemetry (`include_images=False`, real 105 KB BTC fixture + 3 alts @ 25–27 bps)

| Scenario | `QE_D2_10` ReAct Baseline | Phase 3 `DeterministicTradingOrchestrator` | Reduction Factor | Outcome |
| --- | ---: | ---: | ---: | --- |
| **Cycle `c1` (`OPEN_LONG` BTCUSDT)** | 7 LLM calls, `843,994` input chars (`~211k` est. tok) | 2 LLM calls (`Stage 1: 12,824` + `Stage 2: 17,566` = **`30,390` chars**, `~8.9k` local SP tok) | **27.77x** fewer input chars (vs `c2` `1,019,339` chars: **33.54x**) | `DRILL_DOWN(BTCUSDT)` -> `OPEN_LONG` submitted via `HarnessBridge` with SL/TP |
| **Cycle `c4` (`HOLD` active BTC LONG)** | 3 LLM calls, `68,098` input chars | 1 LLM call (**`12,824` chars**, `~3.8k` local SP tok) | **5.31x** vs `c4` (**65.81x** vs `c1`) | Fast Stage-1 `HOLD` (`ACTIVE_POSITION_ON_TRACK`), 0 exchange mutations |

## 5. Operational Runbook (OAuth Proxy & Offline Dry-Run)

```bash
cd /home/layman/QuantumEdge

# 1. Instant offline mock end-to-end verification (0 network, 0 quota, dumps audit to /dev/shm):
.venv/bin/python tools/qe_harness_cli.py run-cycle --transport mock --dry-run --dump-ram

# 2. Dry-run cycle via local Antigravity OAuth proxy (tools/agy_openai_proxy.py on 127.0.0.1:8045):
# .venv/bin/python tools/qe_harness_cli.py run-cycle --transport agy-proxy --proxy-url http://127.0.0.1:8045/v1 --model gemini-4-carbon --dry-run --dump-ram

# 3. Live BingX VST sandbox cycle via OAuth proxy (when authorized by operator):
# .venv/bin/python tools/qe_harness_cli.py run-cycle --transport agy-proxy --model gemini-4-carbon --live-vst --dump-ram
```

## 6. Open Risks Before Phase 4

- **Hourly P1 Re-serialization Without Stateful Checkpoint**: In Phase 3, `StatelessStagePromptAssembler` keeps `P1` byte-identical between Stage 1 and Stage 2 within a single `PreFetchedBundle`, but rebuilds `P1` across separate cycles. Phase 4 (`PrefixContextAssembler` + `SnapshotStore` + `ThesisMemoryStore`) will lock `P0 >= 4,096` tokens and keep `P0 + P1` byte-identical across all intra-hour cycles for 95% Gemini 4 Implicit Caching discount.
- **OAuth Proxy System Overhead**: When running via `agy_openai_proxy.py` (`agy` CLI subprocess), Antigravity injects its CLI system wrapper; `parse_structured_model_json()` and explicit `STAGE1_SCHEMA`/`STAGE2_SCHEMA` in `P0` mitigate formatting drift, while `UsageLedger` tracks exact native token fields per stage.
