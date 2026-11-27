# QE_D2_04_MARKET_DATA_INFRASTRUCTURE_STRATEGY_AND_TUI_BRIEFING_V1

## 1. Key-Value Summary

```text
TASK=QE_D2_04_MARKET_DATA_INFRASTRUCTURE_STRATEGY_AND_TUI_BRIEFING_V1
FINAL_VERDICT=PASS

ORIGIN_MAIN_SHA=94be91c6f03e631f5fa2f0aee60d6e2a4acc7d7e
BASE_REF_USED=origin/feat/qe-direct-cli-sizer-session
BASE_SHA=507e909a78520b0cc4212ae3dcafab6dc709ed94
TARGET_BRANCH=feat/qe-d2-04-market-data-infra

BINGX_REST_RATE_LIMITER_READY=true
OHLCV_RAM_TTL_CACHE_READY=true
LIGHTWEIGHT_POS_ORD_FETCH_NO_BALANCE=true
ADAPTIVE_WATCHER_IDLE_25S_READY=true
WS_FIRST_HUB_SNAPSHOT_BRIDGE_READY=true
RAM_FIRST_REPLAY_STORE_READY=true
HMM_15M_DEFAULT_AND_MACRO_BETA_SIZER_READY=true
AGY_TUI_BRIEFING_CLI_READY=true
P0_LOCAL_SP_TOKENS_AFTER_UPDATE=4550
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_QE04_SUITE=7 passed
PYTEST_FULL_SUITE_NEW_CORE=164 passed, 1 skipped in 32.27s
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

NEXT_GATE=OPERATOR_LOW_API_LIVE_VST_SESSION_WITH_TUI_BRIEFING
```

> [!NOTE]
> At preflight `git fetch origin`, `origin/main` was at `94be91c6f03e631f5fa2f0aee60d6e2a4acc7d7e` (prior to PR merge of `507e909a78520b0cc4212ae3dcafab6dc709ed94`). Per Phase A step 1 instructions, `feat/qe-d2-04-market-data-infra` was branched cleanly from `origin/feat/qe-direct-cli-sizer-session` (`507e909a78520b0cc4212ae3dcafab6dc709ed94`) so all Phases 0–9 + Hotfixes 01–04 are preserved.

## 2. Created & Modified Files

- `src/quantum_edge_core/execution/bingx_vst_adapter.py` (modified):
  - Added thread-safe `ApiBudgetStats` (`rest_calls_total`, `rest_cache_hits`, `rate_limit_retries`, `ws_updates_applied`), `BingXRestRateLimiter` (token-bucket `5.0 req/s`, `burst=8`, jittered exponential retry on BingX `code=100410` / `429`), and `OhlcvRamTtlCache` (`1d: 3600s`, `4h: 900s`, `1h: 300s`, `15m: 60s`, `5m: 30s`, `1m: 15s`).
  - Added `fetch_vst_positions_and_orders()` + `invalidate_vst_status_cache()` skipping `fetch_balance()` (-33% private REST calls) and sharing a thread-safe RAM TTL snapshot invalidated immediately after `cancel_vst_open_orders()` or `move_vst_stop_loss()`.
  - Upgraded `fetch_full_vst_context()` with `ohlcv_cache`, `market_bridge` (WS-First RAM orderbook/ticker read, 0 REST calls when fresh), and `shared_vst_status` (eliminating 9 redundant `fetch_vst_status()` calls per 3-symbol cycle).
- `src/quantum_edge_core/execution/position_protection.py` (modified):
  - Switched `PositionProtectionWatcher.check_once()` to `fetch_vst_positions_and_orders()` (0 `fetch_balance()` calls, preserving monkeypatch compatibility if `fetch_vst_status` is patched).
  - Added adaptive sleep scheduling (`interval_sec = 5.0` when `ACTIVE`, `idle_interval_sec = 25.0` when `IDLE` / `FLAT`, cutting FLAT polling traffic by another 5x).
- `src/quantum_edge_core/market_data/hub_bridge.py` (created):
  - Implemented `HubSnapshotBridge` subscribing via ZMQ SUB (`tcp://127.0.0.1:5555`: `trade.*`, `orderbook.*`, `depth.*`, `market.depth.*`, `kline.*`) over existing `MarketDataHubService` without creating a second WebSocket client or exchange adapter, backed by `RingMarketBuffer` in RAM + `ingest_message()` for deterministic offline testing.
- `src/quantum_edge_core/market_data/replay_store.py` (created):
  - Implemented `MarketSnapshotRecorder` (using `BufferedJsonlArchiver` for zero per-cycle SSD writes) and `MarketReplayReader` reconstructing `list[PreFetchedBundle]` for offline backtest/ablation replay.
- `src/quantum_edge_core/orchestrator/deterministic_orchestrator.py` (modified):
  - Changed `LocalForecastProvider` default training timeframe from `"5m"` to `"15m"` (~25 hours of market structure across 100 bars, with fallback `15m -> 1h -> 5m`).
  - Wired `OhlcvRamTtlCache`, `HubSnapshotBridge`, `MarketSnapshotRecorder`, `BingXRestRateLimiter`, and `ApiBudgetStats` into `PreFetchCoordinator` (plus RAM caching of `BTCUSDT` `15m`/`1h` leader bars), and passed `market_leader` into `ConvictionPositionSizer.size_order()`.
- `src/quantum_edge_core/execution/position_sizer.py` & `src/quantum_edge_core/orchestrator/prompts.py` (modified):
  - Added symmetric Macro-Beta adjustment to `ConvictionPositionSizer`: when `BTC 4h <= -150 bps` and `1d di_diff < 0`, counter-trend `OPEN_LONG` receives `+0.25` `regime_alignment_risk` penalty while aligned `OPEN_SHORT` receives `-0.10` risk reduction (and symmetrically for `BTC 4h >= +150 bps`).
  - Added `Macro-Beta Alignment` rule to `SYSTEM_PROMPT_P0_CORE` (`P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4550 >= 4096`; `P0 + P1 = 9484 <= 9500`).
- `tools/qe_briefing.py` (created) & `tools/run_vst_session.py` (modified):
  - `run_vst_session.py` now updates `/dev/shm/quantumedge/sessions/latest_session.json` atomically in RAM after every cycle, logs `API | rest_calls=... cache_hits=... ws_updates=... rl_retries=... | watcher_mode=...`, uses `watcher.next_sleep_sec`, and supports `--protection-idle-interval-sec`, `--hub-zmq-endpoint`, and `--record-replay`.
  - `qe_briefing.py` renders a crisp Ukrainian Markdown or `--json` briefing from RAM `latest_session.json`, `UsageLedger`, `ThesisMemoryStore`, and `lean_trade_journal.jsonl` (including full last-`N` rationales, 1h/5h IECU & token projections, and BingX API budget metrics) at **0 LLM tokens**.
- `tests/test_qe04_market_data_infra_and_briefing.py` (created):
  - 7 offline tests covering `OhlcvRamTtlCache`, `BingXRestRateLimiter` `100410` retries, lightweight `PositionProtectionWatcher` + adaptive 25s/5s intervals, `HubSnapshotBridge` RAM hits + REST fallback, `MarketSnapshotRecorder`/`MarketReplayReader` zero-immediate-SSD roundtrip, `15m` HMM + Macro-Beta sizer sizing asymmetry, and `qe_briefing.py` Markdown + JSON modes.

## 3. BingX REST Traffic Reduction Estimate (2-Hour Session)

| Subsystem | Before QE-04 (2h) | After QE-04 (2h, FLAT / Mixed) | Reduction |
|---|---:|---:|---:|
| `PositionProtectionWatcher` | ~3,795 calls (`3 req / 5s`) | ~576 calls (`FLAT 25s`, 2 req, 2s TTL) to ~1,440 (`ACTIVE 5s`) | **-62% to -85%** |
| `PreFetchCoordinator` (24 cycles x 3 pairs + BTC) | ~768 calls (redundant status + `1d`/`4h` reloads) | ~240 calls (REST) or **~96 calls** (with `HubSnapshotBridge` WS) | **-69% to -87%** |
| **Total 2h REST Calls** | **~4,563 calls** (`100410` risk) | **~672 – 1,680 calls** (paced by `5 req/s` bucket) | **~3x – 6.8x lower** |

## 4. Verification Results

- `compileall -q src/quantum_edge_core src/quantum_edge_ml tools/run_vst_session.py tools/qe_briefing.py tools/qe_harness_cli.py`: **PASS** (exit 0).
- `P0_LOCAL_SP_TOKENS_AFTER_UPDATE = 4550` (`>= 4096`; `P0 + P1 = 9484 <= 9500`).
- `tests/test_qe04_market_data_infra_and_briefing.py`: **7 passed**.
- Full 22-file regression suite (`--hypothesis-seed=42`, including `tests/test_zero_duplication_guards.py`): **164 passed, 1 skipped in 32.27s** (`0` failures).

## 5. Operator Live VST + Interactive AGY TUI Briefing Commands

**Terminal 1 — Live VST Session Runner (WS-First optional + RAM Replay):**
```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/run_vst_session.py \
  --live-vst \
  --transport agy-direct \
  --model gemini-4-carbon \
  --symbols BNBUSDT,LINKUSDT,SUIUSDT \
  --duration-minutes 120.0 \
  --cycle-interval-sec 180.0 \
  --protection-interval-sec 5.0 \
  --protection-idle-interval-sec 25.0 \
  --be-trigger-pct 0.50 \
  --be-offset-pct 0.20 \
  --record-replay \
  --close-on-exit \
  --report-out reports/tasks/QE_LIVE_QE04_SESSION_REPORT.md \
  --sync-gdrive
```

**Terminal 2 — Instant Human / AGY TUI Briefing (0 OAuth Quota inside trading loop):**
```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/qe_briefing.py --last 5
```

## 6. Compliance & Safety Checklist

- `GIT_PUSH_AUTHORIZED=true` exclusively to `feat/qe-d2-04-market-data-infra` (never `main`, never `--force`).
- `EXTERNAL_REPOS_TOUCHED=false`, `GLOBAL_DOT_HERMES_MODIFIED=false`, `SECRETS_READ_OR_MODIFIED=false`.
- `LIVE_READS=0`, `LIVE_WRITES=0`, `PROVIDER_GETS=0`, `PROVIDER_MUTATIONS=0`, `REAL_TRADING=false`.
- `telegram_notifier.py` untouched; AST Zero-Duplication Guards **100% PASS**.
