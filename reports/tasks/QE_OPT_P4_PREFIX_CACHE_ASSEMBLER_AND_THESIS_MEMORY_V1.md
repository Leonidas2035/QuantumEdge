# QE_OPT_P4_PREFIX_CACHE_ASSEMBLER_AND_THESIS_MEMORY_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P4_PREFIX_CACHE_ASSEMBLER_AND_THESIS_MEMORY_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=f06ac9d4f73ac18fd060151f5e15ed92e6c5c0e2
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
PREFIX_ASSEMBLER_READY=true
THESIS_MEMORY_SQLITE_WAL_AND_RAM_TEMP=true
SNAPSHOT_STORE_RAM_ONLY_RESYNC_READY=true
IMPLICIT_CACHE_EVICTION_MONITOR_READY=true
P0_LOCAL_SP_TOKENS=4156
P0_WITH_PLAYBOOK_LOCAL_SP_TOKENS=4445
P1_LOCAL_SP_TOKENS=4527
P0_PLUS_P1_LOCAL_SP_TOKENS=8683
P2_STAGE1_LOCAL_SP_TOKENS=1157
P2_STAGE2_LOCAL_SP_TOKENS=5423
STAGE1_STAGE2_PREFIX_BYTE_IDENTICAL=true
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE4=7 passed
PYTEST_FULL_SUITE_NEW_CORE=103 passed, 1 skipped in 13.98s
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
NEXT_GATE=QE_OPT_P5_EVENT_DRIVEN_WAKEUP_GATE_AND_POSITION_PROTECTION
```

## 2. Modified & Created Files

- `src/quantum_edge_ml/model_router/router/cache.py` (modified): preserved `CacheEntry` and `RouterCache` for backward compatibility; added `ImplicitCacheEvictionMonitor` tracking `consecutive_stage2_misses`, `cache_token_fraction`, `cache_request_hit_rate`, `alert_active` (`>= 3` consecutive Stage-2 misses), and `reject_explicit_caching()` (`ValueError`).
- `src/quantum_edge_core/orchestrator/prefix_context_assembler.py` (created): implemented `ResyncMode` (`CACHE_HIT`, `LOCAL_RECONSTRUCT`, `RESYNC`), `ThesisMemoryStore` (`CANONICAL_SSD_DURABLE_PATHS["thesis_memory"]`, `apply_sqlite_wal_pragmas` + `PRAGMA foreign_keys = ON`, `upsert_from_decision()`, `compact_expired()`, `render_append_only_block()`), RAM-only `SnapshotStore` (wrapping `ImplicitCacheEvictionMonitor` without duplicating counters), `AssembledPrompt`, and `PrefixContextAssembler` (plus `PrefixPromptAssembler` alias).
- `src/quantum_edge_core/orchestrator/prompts.py` (modified): added concise `QUANTUMEDGE_INTER_HOUR_PLAYBOOK` (BingX Hedge Mode, LiqMap/Orderflow, and Resync protocol reference).
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py` (modified): forwarded `current_seq`, `regime_signature`, and `prompt_tokens` between `DeterministicTradingOrchestrator` and `PrefixContextAssembler` / `StatelessStagePromptAssembler`.
- `src/quantum_edge_core/orchestrator/__init__.py` (modified): exported all Phase 4 symbols.
- `tools/qe_harness_cli.py` (modified): switched `run-cycle` default assembler to `PrefixContextAssembler` + `ThesisMemoryStore` (`--thesis-db` defaulting to `runtime/thesis_memory.sqlite3`, or RAM `/dev/shm/quantumedge/cycles` in `--transport mock --dry-run`; `--stateless` fallback flag; `resync_mode` & `cache_eviction_alert` in JSON output).
- `tests/test_phase4_prefix_cache.py` (created): 7 unit and end-to-end tests covering prefix byte-identity, intra-hour stability, WAL/RAM-temp ThesisMemory, SnapshotStore transitions, eviction monitor, Gemini 4 token thresholds, and CLI `run-cycle`.

## 3. Architectural Decisions

- **3-Layer Byte-Stable Prefix (`P0 / P1 / P2`)**: `P0` (`4,156` local SP tokens; `4,445` with `QUANTUMEDGE_INTER_HOUR_PLAYBOOK`) contains zero dynamic timestamps or UUIDs. `P1` (`4,527` local SP tokens) holds the hourly 4h/1h macro structure across all 4 pairs plus the append-only `THESIS_MEMORY_V1` block. Combined `P0 + P1 = 8,683` tokens (`>= 4,096` and `<= 9,500`), while Stage 2 `P2` is `5,423` tokens (`> 2,050`), satisfying both Gemini 4 Implicit Caching thresholds without artificial padding.
- **RAM-Only `SnapshotStore` + Durable WAL `ThesisMemoryStore`**: `SnapshotStore` checkpoints and `ImplicitCacheEvictionMonitor` state live strictly in RAM (zero disk writes). `ThesisMemoryStore` writes durable theses to SQLite WAL (`synchronous=NORMAL`, `temp_store=MEMORY`, `mmap_size=268435456`) and expires stale entries only during hourly `RESYNC` via `compact_expired()`.
- **OAuth Proxy Text Mode vs Multimodal**: `include_images=False` remains default for `agy-proxy`, delivering `multiscale` + `LIQMAP`/`L2_TOP15` CSVs + 25-bar `OHLCV_TICK_V1` tails on `15m`/`5m`/`1m` in Stage 2.

## 4. Test Evidence & Gemini 4 Token Thresholds

- `compileall -q src/quantum_edge_core/orchestrator src/quantum_edge_ml/model_router/router/cache.py tools/qe_harness_cli.py`: **PASS** (exit 0).
- Full offline pytest suite (16 files, `--hypothesis-seed=42`): **103 passed, 1 skipped** in 13.98s (`test_bingx_vst.py` skipped offline; 0 failures).
- `tests/test_zero_duplication_guards.py`: **5 passed** (confirmed no `cachedContents` usage and zero duplicate modules).

### Measured `estimate_gemini_sp_tokens()` on `REAL_BTC_105KB` + 3 Alts (`include_images=False`)

| Layer | Local SP Tokens | Gemini 4 Threshold | Status |
| --- | ---: | --- | --- |
| `P0` (`build_p0_static_prefix()`) | `4,156` | `>= 4,096` | PASS |
| `P0` + `QUANTUMEDGE_INTER_HOUR_PLAYBOOK` | `4,445` | `>= 4,096` | PASS |
| `P1` (4h/1h macro + `THESIS_MEMORY_V1`) | `4,527` | hourly cached layer | PASS |
| `P0 + P1` (shared prefix) | `8,683` | `[4,096 .. 9,500]` | PASS |
| `P2_S1` (4-pair Stage-1 screening tail) | `1,157` | compact screening | PASS |
| `P2_S2` (1-pair Stage-2 drill-down tail) | `5,423` | `> 2,050` | PASS |

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/pytest tests/test_phase4_prefix_cache.py tests/test_zero_duplication_guards.py -v
.venv/bin/python tools/qe_harness_cli.py run-cycle --transport mock --dry-run --dump-ram
```

## 6. Open Risks Before Phase 5

- **Periodic Polling vs Event-Driven Wakeup**: `qe_harness_cli.py run-cycle` currently executes single cycles on demand; Phase 5 (`EventDrivenWakeupGate` + `PositionProtectionWatcher` + `RingMarketBuffer`) will replace idle polling with RAM-buffered WebSocket triggers and 5-second position protection.
- **Provider Cache-Hit Telemetry Verification**: Actual `cache_read_tokens` discount behavior depends on upstream Gemini 4 / Antigravity OAuth proxy routing and will be observed via `UsageLedger` and `ImplicitCacheEvictionMonitor` during live operator sessions.
