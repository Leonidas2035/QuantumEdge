# QE_OPT_CANARY_01_LIVE_OPTIMIZED_STACK_VIA_OAUTH_PROXY_V1 — Звіт верифікаційної канарки

## 1. Підсумковий блок (`KEY=VALUE`)

```ini
TASK=QE_OPT_CANARY_01_LIVE_OPTIMIZED_STACK_VIA_OAUTH_PROXY_V1
FINAL_VERDICT=BLOCKED

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=77ede3d5595e441ed1c9f1debf38080b92ea58c9
START_UTC=2026-11-27T09:20:22Z
END_UTC=2026-11-27T09:20:47Z
DURATION_MINUTES=0.42

START_EQUITY_VST=260849.9388
END_EQUITY_VST=260849.9388
CANARY_PNL_VST=0.0

CYCLES_RUN_COUNT=0
DRY_RUN_C1_STATUS=FAIL
LIVE_C2_ACTION=SKIPPED
LIVE_C3_ACTION=SKIPPED
PROTECT_ONCE_STATUS=PASS
TRADES_OPENED_COUNT=0
TRADES_CLOSED_COUNT=0
SL_TP_VERIFIED_ON_EXCHANGE=NO_TRADE
FINAL_EXCHANGE_FLAT=false
ORPHAN_ORDERS_REMAINING=1

LEDGER_TOTAL_REQUESTS=0
LEDGER_FAILURE_RATE=0.0
LEDGER_NATIVE_USAGE_COVERAGE=0.0
LEDGER_SUM_PROVIDER_INPUT_TOKENS=0
LEDGER_SUM_CACHE_READ_TOKENS=0
LEDGER_SUM_FRESH_INPUT_TOKENS=0
LEDGER_SUM_OUTPUT_TOKENS=0
LEDGER_SUM_THINKING_TOKENS=0
LEDGER_SUM_IECU=0.0
LEDGER_AVG_IECU_PER_CYCLE=0.0
LEDGER_CACHE_TOKEN_FRACTION=0.0
LEDGER_CACHE_REQUEST_HIT_RATE=0.0
LEDGER_LATENCY_IMPUTED_P95_MS=0.0
AVG_INPUT_CHARS_PER_CYCLE=0
INPUT_CHARS_REDUCTION_VS_QE_D2_10_C1=0.0

PROXY_PROCESS_STOPPED=true
SOURCE_CODE_MODIFIED=false
GIT_PUSHED=false
EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
REAL_TRADING=false

DETAILED_REPORT_FILE=/home/layman/QuantumEdge/reports/tasks/QE_OPT_CANARY_01_LIVE_OPTIMIZED_STACK_VIA_OAUTH_PROXY_V1.md
GDRIVE_SYNC_STATUS=SYNCED
DETECTED_ISSUES_FOR_CODER=Circular ImportError on startup of tools/agy_openai_proxy.py when importing UsageLedger from quantum_edge_ml.model_router.usage_ledger (usage_ledger.py -> quantum_edge_core.execution.__init__ -> bingx_vst_adapter.py -> full_market_context.py -> market_context_serializer.py -> transport.py -> usage_ledger.UsageLedger);Existing open BNBUSDT LONG position (522.67 contracts) and 1 STOP_MARKET order present on BingX VST account at canary preflight
NEXT_GATE=FIX_CIRCULAR_IMPORT_IN_USAGE_LEDGER_AND_RETRY_CANARY_01
```

---

## 2. Хронологія виконання (Phase A — Preflight та зупинка на запуску проксі)

1. **Preflight Git & Порт 8045 (`09:20:22Z`):**
   - Репозиторій `/home/layman/QuantumEdge` на гілці `feat/qe-antigravity-hermes-bridge`, точний коміт `77ede3d5595e441ed1c9f1debf38080b92ea58c9`, робоче дерево чисте.
   - Порт `127.0.0.1:8045` вільний.
2. **Початковий статус BingX VST (`qe_harness_cli.py status`):**
   - `balance_vst`: `total=260849.9388`, `free=203626.5671`, `used=57223.3717`.
   - На акаунті вже була відкрита стороння позиція `BNBUSDT` LONG (`522.67` контрактів, `entry=548.18`, `mark=563.87`, `uPnL=+8196.8885`, `STOP_MARKET` @ `551.03`). Позиція не чіпалась.
3. **Запуск `tools/agy_openai_proxy.py` (БЛОКЕР):**
   - Команда запуску впала з `exit code 1` через циклічний імпорт `ImportError: cannot import name 'UsageLedger' from partially initialized module 'quantum_edge_ml.model_router.usage_ledger'`.
4. **Перевірка `protect-once --dry-run` (`09:20:47Z`):**
   - `qe_harness_cli.py protect-once --dry-run` відпрацював успішно (`exit code 0`), повернувши `{"dry_run": true, "open_positions": 0, "open_orders": 0, "feed_stale": false, "actions": []}`.
5. **Зупинка за правилом безпеки:**
   - Оскільки OAuth-проксі не стартує без правки вихідного коду, перехід до `--dry-run` циклу C1 та `--live-vst` циклів C2/C3 не виконувався.

---

## 3. Таблиця `usage_records` (UsageLedger)

`runtime/usage_ledger_canary01.sqlite3` не створено, оскільки `tools/agy_openai_proxy.py` завершився з `ImportError` до ініціалізації `UsageLedger` (`LEDGER_TOTAL_REQUESTS=0`).

---

## 4. A/B порівняння з базовим `QE_D2_10`

Порівняння неможливе через блокування запуску `agy_openai_proxy.py` на етапі імпорту (`0` циклів `run-cycle` виконано).

---

## 5. Операційний Runbook запуску (статус на `77ede3d5`)

Запуск `tools/agy_openai_proxy.py --ledger-db ...` наразі непрацездатний на `77ede3d5` через порядок імпортів між `quantum_edge_ml.model_router.usage_ledger`, `quantum_edge_core.execution` та `quantum_edge_ml.model_router.backends.transport`. Натомість `tools/qe_harness_cli.py` (де `quantum_edge_core.execution` імпортується першим) запускається без `ImportError`.

---

## 6. Виявлені дефекти та рекомендації для фіксу

1. **Критичний дефект (Circular Import у `agy_openai_proxy.py`):**
   - Ланцюжок імпорту при старті `tools/agy_openai_proxy.py:901`:
     `agy_openai_proxy.py:901` (`from quantum_edge_ml.model_router.usage_ledger import UsageLedger`)
     -> `usage_ledger.py:18` (`from quantum_edge_core.execution.storage_policy import ...`)
     -> `quantum_edge_core/execution/__init__.py:9` (`from ...bingx_vst_adapter import ...`)
     -> `bingx_vst_adapter.py:16` (`from ...full_market_context import ...`)
     -> `full_market_context.py:9` (`from ...market_context_serializer import ...`)
     -> `market_context_serializer.py:26` (`from quantum_edge_ml.model_router.backends.transport import ImageAttachment`)
     -> `transport.py:20` (`from quantum_edge_ml.model_router.usage_ledger import UsageLedger`) -> **`ImportError`**.
   - **Рекомендація:** у `src/quantum_edge_ml/model_router/backends/transport.py` перенести `UsageLedger` під `if TYPE_CHECKING:` (або використати строкову анотацію `"UsageLedger"`), або винести `storage_policy` поза пакет, що тригерить `execution/__init__.py`.
2. **Стан VST-акаунта перед повторним запуском:**
   - На демо-акаунті BingX VST є відкрита позиція `BNBUSDT` LONG (`522.67` контрактів). Перед бойовою канаркою слід врахувати її або закрити вручну, якщо тестується чистий `FLAT`.
