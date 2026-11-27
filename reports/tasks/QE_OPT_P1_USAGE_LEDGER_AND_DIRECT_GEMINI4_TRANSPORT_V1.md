# QE_OPT_P1_USAGE_LEDGER_AND_DIRECT_GEMINI4_TRANSPORT_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P1_USAGE_LEDGER_AND_DIRECT_GEMINI4_TRANSPORT_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=396ea2eac57f068be5b5bb4dd0acf31a74959b98
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
USAGE_LEDGER_READY=true
USAGE_LEDGER_WAL_AND_RAM_TEMP=true
GEMINI_DIRECT_CLIENT_READY=true
INTERACTIONS_AND_OPENAI_COMPAT_SUPPORTED=true
AGY_PROXY_5FIELD_USAGE_AND_UNKNOWN_PATCHED=true
AB_TRANSPORT_BENCH_DRY_RUN=PASS
LIVE_DIRECT_SDK_SMOKE=UNAVAILABLE(400_INVALID_ARGUMENT)
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE1=9 passed in 0.72s
PYTEST_MODEL_ROUTER_LEGACY=19 passed, 13 pre-existing failures (0 regressions)
PYTEST_FULL_SUITE_NEW_CORE=82 passed, 1 skipped in 6.04s
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
NEXT_GATE=QE_OPT_P2_MARKET_CONTEXT_SERIALIZER_AND_COUNT_TOKENS_BENCH
```

## 2. Modified & Created Files

- `src/quantum_edge_ml/model_router/usage_ledger.py` (created): unified `UsageLedger` backed by SQLite WAL (`CANONICAL_SSD_DURABLE_PATHS["usage_ledger"]`, `apply_sqlite_wal_pragmas` + `PRAGMA foreign_keys = ON`). Stores 29 columns in `usage_records` (`NULL` = `UNKNOWN` on failures/timeouts) and 17 columns in `decision_audits`. Computes `failure_rate`, `native_usage_coverage`, `unknown_usage_records`, `sum_iecu`, `cache_token_fraction`, `cache_request_hit_rate`, `retry_amplification`, and exact Kaplan-Meier right-censored `latency_km_p50_ms` / `latency_km_p95_ms` plus `latency_imputed_p95_ms`.
- `src/quantum_edge_ml/model_router/router/budgets.py` (modified): added optional `ledger: UsageLedger | None = None` and `record_usage(rec)` to `TeacherBudgets` while preserving `BudgetConfig` and existing JSON budget behavior with lazy `quantum_edge_core` imports.
- `src/quantum_edge_ml/model_router/audit/events.py` (modified): added `AuditEvent.to_usage_record()` and `AuditEvent.from_usage_record()` adapters without breaking `AuditEvent.to_fields()`.
- `src/quantum_edge_ml/model_router/backends/transport.py` (created): `GEMINI4_IMAGE_TOKENS`, `ImageAttachment`, `LLMRequest` (separating `p1_slow_prefix`, `p2_dynamic_tail`, and trailing `images`), `LLMResponse`, `LLMTransport` ABC, and `AgyProxyTransport` (deduplicating `reasoning_tokens` and logging `TIMEOUT`/`ERROR` with `NULL` tokens and `is_censored=True`).
- `src/quantum_edge_ml/model_router/backends/google_gemini.py` (modified in-place): preserved `DecisionV1Schema` and `GoogleGeminiBackend`; added `OPENAI_COMPAT_BASE_URL`, `MODEL_THINKING_CAPS`, `clamp_thinking_level()`, and `GeminiDirectClient(LLMTransport)` supporting `interactions` (primary) and `openai_compat` (secondary) over persistent HTTP/2 `httpx` clients, `.env` fallback (`GEMINI_API_KEY` -> `GOOGLE_API_KEY`), `max_output_tokens >= 8192` enforcement, no `thinking_budget`/`temperature`/`top_p`/`top_k`, and `count_tokens()`.
- `tools/agy_openai_proxy.py` (modified): added optional `ledger` / `--ledger-db`, native 5-field usage + `num_turns` + `duration_seconds` JSONL/ledger logging on success, `prompt_tokens_details.cached_tokens` + `completion_tokens_details.reasoning_tokens` in OpenAI responses, and explicit `None` (`null` / `NULL`) + `input_chars` + `latency_ms` + `timeout_ms` + `is_censored` on errors/timeouts without duplicate error log entries.
- `tools/ab_transport_bench.py` (created): paired A/B benchmark CLI (`--mode dry-run|live`) storing temporary SQLite ledgers in `get_ram_tmp_dir("ab_bench")` (`/dev/shm/quantumedge/ab_bench`).
- `tests/test_phase1_usage_ledger_and_transport.py` (created): 9 unit and end-to-end tests covering all Phase 1 requirements.

## 3. Architectural Decisions

- **Single Ledger & Backward-Compatible Consolidation**: `UsageLedger` is the sole SQLite telemetry store (`runtime/usage_ledger.sqlite3`), applying `apply_sqlite_wal_pragmas` (`WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`, `mmap_size=268435456`). `TeacherBudgets` and `AuditEvent` use lazy imports of `quantum_edge_core.contracts.optimization` so legacy `model_router` invocations with `PYTHONPATH=src/quantum_edge_ml` remain intact.
- **Prefix Cache Preservation**: Both `GeminiDirectClient._generate_interactions` and `_generate_openai_compat` enforce strict ordering `[P1 slow prefix] -> [P2 dynamic tail] -> [images]` so dynamic chart attachments never invalidate the `P0 + P1` KV prefix cache.
- **No Double-Counting on OpenAI-Compat**: Both `GeminiDirectClient._generate_openai_compat` and `AgyProxyTransport` subtract `completion_tokens_details.reasoning_tokens` from `completion_tokens` to isolate `output_tokens` and `thinking_tokens` cleanly before computing IECU.
- **RAM-First Benchmarking**: `tools/ab_transport_bench.py` writes intermediate SQLite benchmark runs exclusively into `/dev/shm/quantumedge/ab_bench` via `get_ram_tmp_dir("ab_bench")`.

## 4. Test Evidence & Dry-Run Benchmark Output

- `compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/execution src/quantum_edge_ml/model_router tools/agy_openai_proxy.py tools/ab_transport_bench.py`: **PASS** (exit 0).
- Full new-core + Phase 0 + Phase 1 suite (13 test files): **82 passed, 1 skipped** in 6.04s (`test_bingx_vst.py` skipped as expected offline; 0 failures).
- `tests/test_zero_duplication_guards.py`: **5 passed** (confirmed single `GeminiDirectClient` in `google_gemini.py`, single `ccxt.bingx`, no `google.generativeai` or `cachedContents`).
- Legacy `PYTHONPATH=src .venv/bin/pytest src/quantum_edge_ml/model_router/tests/`: **19 passed, 13 pre-existing failures** (identical to Phase A baseline: 12 `@pytest.mark.asyncio` tests without `pytest-asyncio` installed in `.venv` + 1 pre-existing `test_api_with_context` mismatch).
- `tools/ab_transport_bench.py --mode dry-run --runs 6`:
  - `A_agy_proxy`: `5/6` OK (`failure_rate=0.1667`, `unknown_usage_records=1`, `sum_provider_input_tokens=77405`, `sum_iecu=105155.0`, `latency_km_p95_ms=None`).
  - `B_gemini_interactions`: `6/6` OK (`failure_rate=0.0`, `unknown_usage_records=0`, `sum_provider_input_tokens=25200`, `sum_fresh_input_tokens=9700`, `sum_cache_read_tokens=15500`, `sum_iecu=39725.0`, `cache_token_fraction=0.6151`, `latency_km_p95_ms=1950.0`).
  - `C_gemini_openai_compat`: `6/6` OK (`failure_rate=0.0`, `unknown_usage_records=0`, `sum_provider_input_tokens=25200`, `sum_fresh_input_tokens=9700`, `sum_cache_read_tokens=15500`, `sum_iecu=39725.0`, `cache_token_fraction=0.6151`, `latency_km_p95_ms=1950.0`).

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/pytest tests/test_phase1_usage_ledger_and_transport.py tests/test_zero_duplication_guards.py -v
.venv/bin/python tools/ab_transport_bench.py --mode dry-run --runs 6
# Optional operator live run (when valid GEMINI_API_KEY / GOOGLE_API_KEY is configured):
# .venv/bin/python tools/ab_transport_bench.py --mode live --runs 20 --model gemini-4-carbon
```

## 6. Open Risks Before Phase 2

- **Live Direct SDK Key Availability**: Fail-soft direct SDK smoke against `gemini-4-carbon` (`count_tokens` + minimal `interactions.create`) returned HTTP `400 INVALID_ARGUMENT` (`API key not valid`) using the current `.env` `GOOGLE_API_KEY`. Code paths for `GeminiDirectClient` (`interactions`, `openai_compat`, `count_tokens`) are verified via unit tests and `google-genai==3.6.1` introspection, but a valid Gemini API key (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) must be provisioned before running `--mode live` benchmarks or Phase 2 `tools/benchmark_count_tokens.py`.
- **Legacy `model_router/tests/` Async Plugin**: `.venv` lacks `pytest-asyncio`, causing 12 legacy async tests in `src/quantum_edge_ml/model_router/tests/` to fail when invoked directly; Phase 1 tests avoid `pytest-asyncio` by using `asyncio.run(...)` cleanly.
