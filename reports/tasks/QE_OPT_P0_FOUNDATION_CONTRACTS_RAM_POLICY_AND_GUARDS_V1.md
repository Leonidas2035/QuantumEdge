# QE_OPT_P0_FOUNDATION_CONTRACTS_RAM_POLICY_AND_GUARDS_V1

## 1. Key-Value Summary

```text
TASK=QE_OPT_P0_FOUNDATION_CONTRACTS_RAM_POLICY_AND_GUARDS_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=c9de1b0cf40327b81ff8feb9ecc927a5163099f9
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
ROADMAP_MD_MIRROR_CREATED=true
GENAI_SDK_VERSION=3.6.1
GENAI_HAS_INTERACTIONS_API=true
GENAI_HAS_HTTP_OPTIONS=true
OLD_GENERATIVEAI_REMOVED=true
GEMINI_API_KEY_STATUS=NOT_SET
GOOGLE_API_KEY_DOTENV_STATUS=SET
DEV_SHM_AVAILABLE_GB=16.0
RAM_FIRST_STORAGE_POLICY_READY=true
OPTIMIZATION_CONTRACTS_READY=true
ZERO_DUPLICATION_GUARDS_PASS=true
COMPILEALL_STATUS=PASS
PYTEST_PHASE0=12 passed in 0.25s
PYTEST_FULL_SUITE_NEW_CORE=73 passed, 1 skipped in 6.01s
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
NEXT_GATE=QE_OPT_P1_USAGE_LEDGER_AND_DIRECT_GEMINI4_TRANSPORT
```

## 2. Modified/Created Files & Interface Audit

### Created / Modified Files
- `Documentation/QE_OPTIMIZATION_MASTER_ROADMAP_V1.md` (created): clean Markdown mirror of `Documentation/Дорожня Карта Оптимізації QuantumEdge Hybrid.docx` (496 lines, preserving headings, lists, tables, and code blocks; original `.docx` untouched).
- `requirements.txt` (modified): removed `google-generativeai==0.8.6`; upgraded `google-genai[httpx]>=3.6.1`; added `h2>=4.1.0`, `Pillow>=10.4.0,<13`, `mplfinance>=0.12.10b0`, `lightgbm>=4.5.0`, `hmmlearn>=0.3.3`, `arch>=7.0.0`, `scipy>=1.14.0`, `hypothesis>=6.112.0`.
- `src/quantum_edge_core/contracts/optimization.py` (created): `TransportChannel`, `RequestStatus`, `ThinkingLevel`, `UsageRecordV1` (`fresh_input_tokens`, `iecu`), `DecisionAuditV1`.
- `src/quantum_edge_core/contracts/lean.py` (modified): non-invasive re-export of optimization contracts at module bottom; `MarketSnapshot`, `TradeDecision`, `OrderResult`, `FullMarketContext` unchanged.
- `src/quantum_edge_core/contracts/__init__.py` (modified): re-exports optimization contracts alongside lean contracts.
- `src/quantum_edge_core/execution/storage_policy.py` (created): `StorageTier`, `CANONICAL_SSD_DURABLE_PATHS`, `get_ram_tmp_dir()`, `apply_sqlite_wal_pragmas()`, `RingMarketBuffer`, `BufferedJsonlArchiver`.
- `tests/test_zero_duplication_guards.py` (created): AST guards for active optimized contour.
- `tests/test_phase0_foundation.py` (created): 7 unit tests for contracts and RAM-first storage policy.

### Read-Only Interface Audit (Phase 1 Preparation)
1. `src/quantum_edge_ml/model_router/backends/base.py`:
   - `Backend(Protocol)`: `name: str`, `generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str`.
2. `src/quantum_edge_ml/model_router/backends/google_gemini.py`:
   - `DecisionV1Schema(BaseModel)`: `v`, `s`, `c`, `sl`, `tp`, `r`, `rk`.
   - `GoogleGeminiBackend`: `__init__(self, transport=None)`, `async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str` using `self.client = genai.Client(api_key=...)` and `self.client.aio.models.generate_content(...)`.
3. `src/quantum_edge_ml/model_router/audit/events.py`:
   - `AuditEvent` dataclass (`ts_utc`, `sym`, `backend_used`, `model_id`, `mode`, `decision_json`, `confidence`, `risk`, `latency_ms`, `prompt_hash`, `ok`, `error_code`, `to_fields()`).
4. `src/quantum_edge_ml/model_router/router/budgets.py`:
   - `BudgetConfig(max_requests_per_day, max_tokens_per_day)` and `TeacherBudgets(path, config)` (`can_use`, `record`, `remaining`).
5. `src/quantum_edge_ml/model_router/router/cache.py`:
   - `CacheEntry(decision_json, backend, created_ts)` and `RouterCache(path, ttl_s)` (`get`, `set`, `delete` backed by SQLite).
6. `src/quantum_edge_ml/model_router/router/circuit.py`:
   - `CircuitConfig(failure_threshold, window_s, cool_down_s)` and `CircuitBreaker(name, config, state_path)` (`is_open`, `record_failure`, `record_success`, `snapshot`).
7. `src/quantum_edge_core/contracts/lean.py`:
   - `MarketSnapshot`, `TradeDecision`, `OrderResult`, `FullMarketContext` (all `ConfigDict(extra="ignore")`).
8. Legacy `google.generativeai` & Zero-Duplication scan:
   - `grep -rn "generativeai" src/ tools/ tests/` returned 0 matches.
   - `ccxt.bingx` exists only in `src/quantum_edge_core/execution/bingx_vst_adapter.py`.
   - `BingXLiveFeed` is defined in `src/quantum_edge_core/market_data/feeds/bingx.py`.
   - `LeanExecutor` and `LeanRiskGuard` exist only in `src/quantum_edge_core/execution/{lean_executor,risk_guard}.py`.

## 3. Architectural Decisions

- **RAM-First / SSD-Wear Protection (`storage_policy.py`)**: Host `ARTLINE-PC` provides `/dev/shm` tmpfs (`16G` available). `get_ram_tmp_dir()` targets `/dev/shm/quantumedge` with a safe fallback to `tempfile.gettempdir()/quantumedge_ram`. `RingMarketBuffer` keeps streaming market data in `collections.deque(maxlen=...)` in RAM. `BufferedJsonlArchiver` accumulates JSONL records in RAM and defers SSD writes until batch thresholds (`flush_max_items`, `flush_interval_sec`) or explicit `flush()`/`close()`. `apply_sqlite_wal_pragmas()` sets `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`, and `mmap_size=268435456` for durable SQLite databases (`lean_orders.sqlite3`, `usage_ledger.sqlite3`, `thesis_memory.sqlite3`).
- **AST Zero-Duplication Guards (`tests/test_zero_duplication_guards.py`)**: Scoped strictly to the active optimized contour (`contracts`, `execution`, `orchestrator`, `market_data`, `model_router`, `forecasting`, `tools/{qe_harness_cli,agy_openai_proxy}.py`) so legacy bots and `hermes_agent/` do not cause false positives.
- **Google GenAI SDK v3.6.1**: Verified `google-genai==3.6.1` installed with `client.aio.interactions` (`True`) and `types.HttpOptions` (`True`). `Pillow` constrained to `>=10.4.0,<13` to maintain compatibility with `streamlit==1.59.1`.
- **Venv Repair**: Restored `.venv/pyvenv.cfg` and `.venv/bin/python{,3}` symlinks to `python3.12` so `.venv/bin/pytest` and `.venv/bin/pip` execute cleanly.

## 4. Test Evidence, Package Versions & `/dev/shm` State

- `compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/execution`: PASS (exit 0).
- `pytest` (full new-core + Phase 0 suite, 12 files): **73 passed, 1 skipped** in 6.01s (`test_bingx_vst.py` skipped as expected in offline mode; 0 failures).
- `/dev/shm`: `tmpfs 16G total, 14M used, 16G avail (1% use)`.
- Installed key package versions in `.venv`:
  - `google-genai==3.6.1`, `httpx==0.28.1`, `httpx2==2.16.2`, `h2==4.4.1`
  - `Pillow<13` (downgraded from 13.0.0 for streamlit compatibility), `mplfinance==0.12.10b0`
  - `lightgbm==4.7.0`, `hmmlearn==0.3.3`, `arch==9.0.0`, `scipy==1.18.1`, `hypothesis==6.170.17`

## 5. Operational Runbook

```bash
cd /home/layman/QuantumEdge
.venv/bin/python -m compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/execution
.venv/bin/pytest tests/test_zero_duplication_guards.py tests/test_phase0_foundation.py -v
```

## 6. Open Risks Before Phase 1

- **API Key Naming**: `GEMINI_API_KEY` is `NOT_SET` in process env and `.env`, whereas `GOOGLE_API_KEY` is `SET` in `.env` (not exported in bare shell). Phase 1 `GeminiDirectClient` / `UsageLedger` harness must load `.env` or support `GOOGLE_API_KEY` fallback consistently (matching existing `GoogleGeminiBackend`).
- **Legacy `model_router` Coupling**: `Router` in `src/quantum_edge_ml/model_router/router/router.py` uses `TeacherBudgets` (JSON file) and `RouterCache` (separate SQLite) with `Backend.generate(prompt, *, system_prompt, timeout_s) -> str`. Phase 1 must wrap `audit.py` and `budgets.py` cleanly as thin facades over `UsageLedger` without breaking existing `model_router/tests/`.
