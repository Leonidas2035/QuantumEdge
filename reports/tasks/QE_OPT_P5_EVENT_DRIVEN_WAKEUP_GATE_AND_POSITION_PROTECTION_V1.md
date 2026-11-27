# QE_OPT_P5_EVENT_DRIVEN_WAKEUP_GATE_AND_POSITION_PROTECTION_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P5_EVENT_DRIVEN_WAKEUP_GATE_AND_POSITION_PROTECTION_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=f11853e24244ddb98b160f04695de75be03cda09
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
HUB_ZMQ_FORMAT_MATCHED=DUAL
HAWKES_O1_MAX_ABS_ERROR=4.44e-16
COALESCING_700MS_VERIFIED=true
SHADOW_LOGGER_RAM_BUFFERED_VERIFIED=true
POSITION_PROTECTION_ZERO_LLM_VERIFIED=true
POSITION_PROTECTION_SINGLE_WRITER_VERIFIED=true
SHADOW_72H_MATERIAL_RECALL=1.0
SHADOW_72H_FALSE_WAKE_RATE=0.0
SHADOW_72H_MISSED_OPP_RATE=0.0
SHADOW_72H_PROJECTED_MIXED_WAKES_PER_DAY=42.0
CLI_PROTECT_ONCE_DRY_RUN=PASS
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE5=10 passed
PYTEST_FULL_SUITE_NEW_CORE=113 passed, 1 skipped in 14.57s
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
NEXT_GATE=QE_OPT_P6_CAUSAL_FORECAST_PIPELINE_REASONING_ROUTER_AND_ACCEPTANCE_MATRIX
```

## 2. Modified & Created Files & MarketDataHub ZMQ Audit

### MarketDataHub ZMQ Audit Findings (`Phase A`)
- `src/quantum_edge_core/market_data/ipc/publisher.py` (`ZmqPublisher`, default `tcp://*:5555`) encodes `BaseEvent` via `EventCodec.encode()` (`msgspec.json.Encoder` producing UTF-8 JSON with `"type"` tag, `symbol`, `mid_price`, `mid`, `price`, `close`, `spread`, `imbalance_top10`, `timestamp`), while `models/events.py` (`encode_event`) and `OrderBookAggregator._publish_depth` emit `ts_ns` and MessagePack frames.
- `BingXLiveFeed` (`feeds/bingx.py`) sets `event_type="depth"` on `OrderBookUpdate` (broadcast by `hub.py` on `depth.<symbol>`), `event_type="trade"` (`trade.<symbol>`), and `event_type="kline"` (`kline.<symbol>`), while `OrderBookAggregator` publishes on `market.depth.<symbol>`.
- `EventDrivenWakeupGate.run_zmq_subscriber()` subscribes to all 6 canonical topic prefixes (`trade.`, `depth.`, `orderbook.`, `market.depth.`, `kline.`, `liquidation.`) and decodes frames via dual `decode_zmq_payload()` (`msgspec.json` + `msgspec.msgpack` fallback).

### Modified & Created Files
- `src/quantum_edge_core/contracts/optimization.py`, `lean.py`, `__init__.py` (modified): added `DecisionEventV1` (`event_id`, `ts_utc`, `symbols`, `reasons`, `priority`, `metrics_snapshot`) with non-invasive re-exports.
- `src/quantum_edge_core/market_data/wakeup_gate.py` (created): `HawkesTracker` ($O(1)$ recursion), `SymmetricCusumFilter` (causal pre-shock EWMA threshold + spread-scaled floor + `k_drift=0.05`), `ShadowWakeupLogger` (backed by `BufferedJsonlArchiver`), `EventDrivenWakeupGate` (5 triggers, `>= 2.5x` spread filter on liq magnets, 90s cooldown + hysteresis + `CRITICAL`/`HEARTBEAT_1H` bypass, 700ms coalescing window, per-symbol `RingMarketBuffer`, non-blocking poller `run_zmq_subscriber()`).
- `src/quantum_edge_core/execution/position_protection.py` & `execution/__init__.py` (created/modified): autonomous 5-second `PositionProtectionWatcher` (`run_forever()`, `check_once()`, `touch_feed_heartbeat()`, `stop()`) with 0 LLM imports, FLAT orphan cleanup via `cancel_vst_open_orders(..., "ALL")`, and emergency closes strictly via `HarnessBridge.submit_decision()`.
- `tools/qe_harness_cli.py` (modified): added `protect-once` subcommand (`--dry-run` default / `--live-vst`, `--stale-limit-sec`, `--max-liq-pct`, `--mock-status-json`).
- `tools/eval_wakeup_shadow.py` (created): 72-hour multi-regime shadow replay evaluator writing exclusively to `get_ram_tmp_dir("phase5_shadow")` (`/dev/shm/quantumedge/phase5_shadow`).
- `tests/test_phase5_wakeup_and_protection.py` (created): 10 unit, ZMQ SUB, AST-isolation, CLI, and 72h shadow replay tests.

## 3. Architectural Decisions

- **RAM-First / SSD-Wear Protection**: Streaming tick snapshots inside `EventDrivenWakeupGate` live strictly in fixed-capacity `RingMarketBuffer(maxlen=256)` in RAM. `ShadowWakeupLogger` wraps `BufferedJsonlArchiver` so suppressed/shadow events accumulate in RAM and flush to disk only in batches (`flush_max_items` / `flush_interval_sec` / explicit `flush()`/`close()`).
- **Spread-Aware CUSUM & LiqMagnet Filtering**: Scaling the CUSUM minimum threshold above `1.6x spread_bps` alongside `check_liq_magnet` (`dist_bps >= 2.5 * spread_bps`) eliminates sub-spread random-walk false wakes on wide-spread VST altcoins (`SOLUSDT` `26 bps` spread) while preserving `100%` recall on material `>= 60 bps` impulses and valid magnet sweeps.
- **Complete Safety-Plane Isolation**: `PositionProtectionWatcher` has zero imports from `quantum_edge_ml` / `model_router` / `genai` (verified via AST test) and routes all emergency exits through the single canonical `HarnessBridge -> LeanExecutor -> OrderLedger` writer.

## 4. Test Evidence & 72-Hour Shadow Replay Metrics

- `compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/market_data/wakeup_gate.py src/quantum_edge_core/execution/position_protection.py tools/qe_harness_cli.py tools/eval_wakeup_shadow.py`: **PASS** (exit 0).
- Full offline pytest suite (17 files, `--hypothesis-seed=42`): **113 passed, 1 skipped** in 14.57s (0 failures).
- `HawkesTracker` $O(1)$ vs exact $O(N)$ sum over 200 irregular trade arrivals: max absolute error **`4.44e-16`** (`< 1e-12`).
- `tools/eval_wakeup_shadow.py --hours 72 --seed 42` (`BTCUSDT` @ `0.25 bps` + `SOLUSDT` @ `26.0 bps`):

| Metric | Measured Value | Roadmap Target | Status |
| --- | ---: | --- | --- |
| `material_event_recall` | `1.0000` (`36/36`) | `>= 0.90` | PASS |
| `false_wake_rate` | `0.0000` (`0/64` signal wakes) | `<= 0.25` | PASS |
| `missed_opportunity_rate` | `0.0000` (`0/36`) | `<= 0.10` | PASS |
| `quiet_wakes_per_day` | `29.0` / day | vs `144.0` baseline (`4.97x` lower) | PASS |
| `mixed_wakes_per_day` | `42.0` / day | vs `144.0` baseline (`3.43x` lower) | PASS |
| `volatile_wakes_per_day` | `64.0` / day | vs `144.0` baseline (`2.25x` lower) | PASS |

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/pytest tests/test_phase5_wakeup_and_protection.py tests/test_zero_duplication_guards.py -v
.venv/bin/python tools/eval_wakeup_shadow.py --hours 72 --seed 42
.venv/bin/python tools/qe_harness_cli.py protect-once --dry-run
```

## 6. Open Risks Before Phase 6

- **Quantitative Forecast Inputs**: `hmm_regime_delta` in `EventDrivenWakeupGate` and `forecasts_by_symbol` in `DeterministicTradingOrchestrator` currently accept optional external inputs; Phase 6 (`ForecastPipeline`: `CausalHMMFilter` + `EGARCH` + `LightGBM` + `AdaptiveConformalCalibrator`) will supply calibrated local forecasts with zero lookahead leakage.
- **Static Stage-2 `thinking_level=MEDIUM`**: Phase 6 (`ReasoningEffortRouter`) will replace the fixed `MEDIUM` Stage-2 default with hysteresis-stabilized `DecisionComplexityScore` routing (`LOW`/`MEDIUM`/`HIGH`) and verify all 12 gates in `tools/verify_acceptance_matrix.py`.
