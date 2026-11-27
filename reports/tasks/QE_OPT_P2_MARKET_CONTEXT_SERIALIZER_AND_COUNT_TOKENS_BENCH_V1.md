# QE_OPT_P2_MARKET_CONTEXT_SERIALIZER_AND_COUNT_TOKENS_BENCH_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P2_MARKET_CONTEXT_SERIALIZER_AND_COUNT_TOKENS_BENCH_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=77f17925869f64599fd86f35d51bf2b2627f6318
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
TICK_DELTA_EXACT_ROUNDTRIP_PASS=true
DELTA_BPS_MAX_CLOSE_ERR_BPS=0.4947
WEBP_SCREENING_RENDER_MS=129.45
WEBP_CANDIDATE_5TF_RENDER_MS=215.29
WEBP_ZERO_DISK_WRITES_VERIFIED=true
BENCH_FIXTURE_SOURCE=REAL_BTC_105KB
BENCH_PRETTY_LOCAL_SP_TOKENS=60323
BENCH_TICK_DELTA_RATIO=4.75
BENCH_DELTA_BPS_RATIO=8.59
BENCH_MULTISCALE_RATIO=16.63
BENCH_HYBRID_WEBP_RATIO=12.94
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE2=6 passed (500+500 Hypothesis examples, seed=42)
PYTEST_FULL_SUITE_NEW_CORE=88 passed, 1 skipped in 13.30s
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
NEXT_GATE=QE_OPT_P3_DETERMINISTIC_ORCHESTRATOR_AND_PROGRESSIVE_DISCLOSURE
```

## 2. Modified & Created Files

- `src/quantum_edge_core/execution/market_context_serializer.py` (created): unified `MarketContextSerializer` + `SerializedMarketPayload` consolidating `tick_delta` (`OHLCV_TICK_V1`), `delta_bps` (`DELTA_BPS_V1`), `multiscale`, `hybrid_webp`, `encode_liqmap_csv`, `encode_orderbook_csv`, and RAM-only `render_screening_grid_webp` / `render_candidate_5tf_webp`.
- `src/quantum_edge_core/execution/bingx_vst_adapter.py` (modified): added `extract_instrument_specs(client_or_markets)` supporting `ccxt.TICK_SIZE` (4) and `ccxt.DECIMAL_PLACES` (2) with zero network calls when `markets` is populated.
- `src/quantum_edge_core/execution/full_market_context.py` (modified): added `serialize_for_llm(ctx, *, mode="hybrid_webp", ohlcv_tail=25, include_full_liqmap=True, serializer=None)` facade without modifying `FullMarketContext`.
- `src/quantum_edge_core/execution/__init__.py` (modified): re-exported `MarketContextSerializer`, `SerializedMarketPayload`, `extract_instrument_specs`, and `serialize_for_llm`.
- `tools/benchmark_count_tokens.py` (created): offline/hybrid CountTokens benchmark supporting `--counter local-sp|agy-proxy|direct-api` (`local-sp` default with Gemini SentencePiece `split_digits=True` estimation and `GEMINI4_IMAGE_TOKENS`) and storing JSON outputs in `get_ram_tmp_dir("phase2_bench")`.
- `tests/test_phase2_serializer_roundtrip.py` (created): Hypothesis 500-example round-trip and error-bound tests, 4-mode `FullMarketContext` tests on `runtime/step1_enriched_ctx_btcusdt.json`, RAM-only WebP verification, CCXT spec extraction, and CLI benchmark test.

## 3. Architectural Decisions

- **Real BingX BTCUSDT Precision Handling**: Inspection of `runtime/step1_enriched_ctx_btcusdt.json` (105,040 B) revealed `volume` values quantized to `0.0001` (`448` bars finer than baseline `0.001`). `encode_ohlcv_tick_delta` strictly enforces explicit `tick_size`/`qty_step` (raising `ValueError` on non-multiples) while auto-refining default steps to the series' finest decimal scale when `tick_size`/`qty_step` are `None`, preserving `0.0` Decimal round-trip error across real fixtures.
- **Error-Feedback `DELTA_BPS_V1` Uniform Bound**: Under 1-bps integer quantization around reconstructed `rec_c`, worst-case step error relative to `rec_c` is strictly `<= 0.50 bps`; relative to `orig["close"]` under `+/-1.5%` bar returns (`delta_bps_series` Hypothesis generator), the mathematical bound is `0.5 / (1 - 0.015) = 0.5076 bps` (tested at `<= 0.51 bps`), and measured max close error on `runtime/step1_enriched_ctx_btcusdt.json` is `0.4947 bps`.
- **RAM-Only WebP Rendering**: `render_screening_grid_webp` (`medium`, 560 tok) and `render_candidate_5tf_webp` (`high`, 1120 tok) render directly via `matplotlib.use("Agg")` -> `fig.canvas.buffer_rgba()` -> `PIL.Image.fromarray` -> `io.BytesIO` WebP (`method=1`), closing `fig` in `try/finally` with zero disk writes.
- **Offline-First Benchmark**: Default `--counter local-sp` requires zero API keys and zero Antigravity OAuth quota, clearly tagging outputs as `local_sp_estimate`.

## 4. Test Evidence & CountTokens Benchmark Table

- `compileall -q src/quantum_edge_core/execution tools/benchmark_count_tokens.py`: **PASS** (exit 0).
- Full pytest suite (14 files, `--hypothesis-seed=42`): **88 passed, 1 skipped** in 13.30s (`test_bingx_vst.py` skipped offline; 0 failures).
- WebP steady-state render latency on `runtime/step1_enriched_ctx_btcusdt.json`:
  - `render_screening_grid_webp` (2x2 `medium`, 560 tok): **129.45 ms** (`< 150 ms`).
  - `render_candidate_5tf_webp` (5-TF + LiqMap `high`, 1120 tok): **215.29 ms** (`200.54 ms` inside `benchmark_count_tokens.py`).
- `tools/benchmark_count_tokens.py --counter local-sp` on `REAL_BTC_105KB` (`runtime/step1_enriched_ctx_btcusdt.json`):

| Format | Text Bytes | Text Tok (`local_sp`) | Img Tok | Total Tok | Ratio vs Pretty | MaxErr (bps) | Render (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `json_pretty_baseline` | 105,040 | 60,323 | 0 | 60,323 | 1.00x | - | 0.00 |
| `json_minified` | 60,561 | 51,042 | 0 | 51,042 | 1.18x | - | 0.00 |
| `tick_delta` | 13,195 | 12,707 | 0 | 12,707 | 4.75x | 0.0000 | 3.68 |
| `delta_bps` | 7,523 | 7,020 | 0 | 7,020 | 8.59x | 0.4947 | 1.62 |
| `multiscale` | 4,325 | 3,627 | 0 | 3,627 | 16.63x | 0.4947 | 2.38 |
| `hybrid_webp` | 4,169 | 3,541 | 1,120 | 4,661 | 12.94x | 0.0000 | 200.54 |

- 4-Pair Projection: Pretty = `221,988` tok vs. Stage 1 (`4-pair + 2x2 WebP`) = `2,917` tok and Stage 2 (`1-pair hybrid_webp`) = `4,661` tok.

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/pytest tests/test_phase2_serializer_roundtrip.py tests/test_zero_duplication_guards.py -v --hypothesis-seed=42
.venv/bin/python tools/benchmark_count_tokens.py --counter local-sp
# Synthetic 44-level liqmap test fixture:
.venv/bin/python tools/benchmark_count_tokens.py --counter local-sp --synthetic
```

## 6. Open Risks Before Phase 3

- **Local SentencePiece vs Provider Calibration**: `local-sp` uses regex-based `split_digits=True` estimation (`local_sp_estimate`); exact provider `count_tokens()` calibration awaits an active `GEMINI_API_KEY` or `--counter agy-proxy` operator check.
- **5-TF WebP Render Latency**: `render_candidate_5tf_webp` runs at `~200–215 ms` for 6 subplots on CPU (`Agg`); this is acceptable for Stage 2 drill-down (`<= 1` call per cycle), while Stage 1 screening `render_screening_grid_webp` is `129.45 ms`.
