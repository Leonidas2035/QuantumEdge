# QE_OPT_HOTFIX_01_CIRCULAR_IMPORT_SYMBOL_SCOPING_AND_GITHUB_PUSH_V1 — Звіт хотфіксу та релізної гігієни

## 1. Підсумкові метрики (`KEY=VALUE`)

```ini
TASK=QE_OPT_HOTFIX_01_CIRCULAR_IMPORT_SYMBOL_SCOPING_AND_GITHUB_PUSH_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=77ede3d5595e441ed1c9f1debf38080b92ea58c9
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
WORKTREE_CLEAN=true

CIRCULAR_IMPORT_FIXED=true
COLD_START_PROXY_HELP_OK=true
COLD_START_USAGE_LEDGER_IMPORT_OK=true
POSITION_PROTECTION_SYMBOL_SCOPED=true
POSITION_PROTECTION_CANCEL_ALL_REMOVED=true
ZERO_DUPLICATION_GUARDS_PASS=true
PRE_PUSH_SECRET_SCAN_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_FULL_SUITE_NEW_CORE=124 passed, 1 skipped in 17.15s
TOTAL_FAILED=0

AUDIT_BUNDLE_SYNCED_TO_GDRIVE=true
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

DETAILED_REPORT_FILE=/home/layman/QuantumEdge/reports/tasks/QE_OPT_HOTFIX_01_CIRCULAR_IMPORT_SYMBOL_SCOPING_AND_GITHUB_PUSH_V1.md
GDRIVE_SYNC_STATUS=SYNCED
NEXT_GATE=DEEP_CODE_AUDIT_BY_ARCHITECT_AND_RETRY_QE_OPT_CANARY_01
```

---

## 2. Хотфікс 1: Розрив циклічного імпорту `UsageLedger` (Phase B)

- **Причина дефекту:** При холодному старті `tools/agy_openai_proxy.py` імпортував `UsageLedger` до завантаження `quantum_edge_core.execution`, що створювало цикл:
  `agy_openai_proxy.py` -> `usage_ledger.py` -> `quantum_edge_core.execution.storage_policy` -> `execution/__init__.py` -> `bingx_vst_adapter.py` -> `full_market_context.py` -> `market_context_serializer.py` -> `transport.py` -> `usage_ledger.UsageLedger`.
- **Виправлення:**
  - У `src/quantum_edge_ml/model_router/backends/transport.py` та `src/quantum_edge_ml/model_router/backends/google_gemini.py` імпорт `from quantum_edge_ml.model_router.usage_ledger import UsageLedger` перенесено під `if TYPE_CHECKING:` (за наявності `from __future__ import annotations`).
- **Верифікація холодного старту:**
  - `PYTHONPATH=src .venv/bin/python -c "from quantum_edge_ml.model_router.usage_ledger import UsageLedger; print(UsageLedger.__name__)"` -> `UsageLedger` (exit 0).
  - `.venv/bin/python tools/agy_openai_proxy.py --help` -> exit 0.
  - `.venv/bin/python tools/qe_harness_cli.py --help` -> exit 0.

---

## 3. Хотфікс 2: Ізоляція `PositionProtectionWatcher` по `allowed_symbols` (Phase C)

- **Зміни в `src/quantum_edge_core/execution/position_protection.py`:**
  - Додано параметр `allowed_symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")` (зберігається як `frozenset[str]` у верхньому регістрі).
  - У `check_once()` усі `open_positions` та `open_orders` фільтруються виключно за `symbol in self.allowed_symbols`. Сторонні символи (напр. `BNBUSDT`) ігноруються та фіксуються лише у лічильниках `ignored_foreign_positions` / `ignored_foreign_orders`.
  - Видалено небезпечний виклик `cancel_vst_open_orders(self.client, "ALL")`: очищення орфанів у стані FLAT тепер виконується суворо посимвольно (`for sym in orphan_symbols: cancel_vst_open_orders(self.client, sym)`).
- **Зміни в `tools/qe_harness_cli.py` (`protect-once`):**
  - Додано `--allowed-symbols` (резерв: `QE_ALLOWED_SYMBOLS` -> `RiskConfig().allowed_symbols`) і `--read-live-vst` (read-only перевірка реального VST у режимі `--dry-run`).
  - Додано явне поле `"source"` у JSON-вивід (`"mock_status"`, `"paper_client"`, `"bingx_vst_readonly"`, `"bingx_vst_live"`).

---

## 4. Регресійні тести та `compileall` (Phase D)

- Додано `test_cold_start_subprocess_imports_no_circular_import` у `tests/test_phase1_usage_ledger_and_transport.py`.
- Додано `test_position_protection_ignores_foreign_symbols_and_never_cancels_all` у `tests/test_phase5_wakeup_and_protection.py`.
- `compileall` по всьому новому контуру: **PASS**.
- Повний набір офлайн-тестів (18 тестових файлів, включно з `test_zero_duplication_guards.py`): **`124 passed, 1 skipped in 17.15s`** (`TOTAL_FAILED=0`).

---

## 5. Code Audit Bundle та Pre-Push перевірка на секрети (Phase E)

- Зібрано консолідований аудит-бандл `/dev/shm/quantumedge/QE_OPT_P0_P6_CODE_AUDIT_BUNDLE_V1.md` (`221,009` байт, 15 ключових файлів Фаз 0–6) та синхронізовано у Google Drive `QuantumEdge-Reports` (без додавання в Git).
- Проведено pre-push аудит файлів гілки `feat/qe-antigravity-hermes-bridge` відносно `origin/main`: відсутні `.env*`, `*.key`, `*.pem`, `rclone.conf`, `runtime/*.sqlite3`, `runtime/*.jsonl` та захардкоджені секрети.

---

## 6. Стан Git та Push гілки `feat/qe-antigravity-hermes-bridge`

- Локальний коміт зафіксовано на `feat/qe-antigravity-hermes-bridge` (без торкання `main` і без `--force`).
- Деталі пушу та фінальний `HEAD` наведено у підсумковому блоці відповіді.
