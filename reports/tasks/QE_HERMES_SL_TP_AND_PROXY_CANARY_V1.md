# QE_HERMES_SL_TP_AND_PROXY_CANARY_V1

## 1. KEY=VALUE
```text
TASK=QE_HERMES_SL_TP_AND_PROXY_CANARY_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=0fd564fdf82dfde4ec07c565ed74498353af4be4
START_UTC=2026-11-25T22:32:47Z
END_UTC=2026-11-25T22:37:34Z
DURATION_MINUTES=4.78

START_EQUITY_VST=261042.0767
END_EQUITY_VST=261041.6625
ESTIMATED_CANARY_PNL_VST=-0.4142

XRPUSDT_DRY_RUN_ALLOWED=true
BTC_OPEN_FILLED=true
REQUESTED_SL_PRICE=59562.5
OBSERVED_SL_TRIGGER_PRICE=59562.5
REQUESTED_TP_PRICE=61066.6
OBSERVED_TP_TRIGGER_PRICE=61066.6
SL_TP_VERIFIED_ON_EXCHANGE=true
MINIMAL_CLOSE_SCHEMA_VERIFIED=true
REDUCE_ONLY_CLOSE_VERIFIED=true
FINAL_EXCHANGE_FLAT=true
ORPHAN_ORDERS_REMAINING=0

PROXY_CALLS_COUNT=7
PROXY_BROKEN_PIPE_COUNT=0
PROXY_MAX_LATENCY_SEC=32.85
CLI_OR_EXCHANGE_ERRORS_COUNT=0

DETAILED_REPORT_FILE=/home/layman/QuantumEdge/reports/tasks/QE_HERMES_SL_TP_AND_PROXY_CANARY_V1.md
SOURCE_CODE_MODIFIED=false
GIT_PUSHED=false
EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
REAL_TRADING=false

DETECTED_ISSUES_FOR_CODER=NONE
NEXT_GATE=START_BOUNDED_1H_AUTONOMOUS_VST_SESSION_OR_BUILD_LOCAL_ML_LAYER
```

## 2. Перевірені інтерфейси та команди
Уся канарка виконана автономно ізольованим Hermes (`HERMES_HOME=/home/layman/QuantumEdge/runtime/hermes_ag_home`, `runtime/hermes_venv`) через активний локальний проксі `http://127.0.0.1:8045/v1` (`gemini-4-carbon-medium`, `--timeout 180`) виключно через [`tools/qe_harness_cli.py`](file:///home/layman/QuantumEdge/tools/qe_harness_cli.py):
- `qe_harness_cli.py status` (preflight, mid-flight після відкриття, final FLAT check);
- `qe_harness_cli.py context --symbol XRPUSDT --out runtime/canary_ctx_XRPUSDT.json` — підтверджено `risk_limits.allowed_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]` та `last_price = 0.9374`;
- `qe_harness_cli.py execute --dry-run` для `XRPUSDT OPEN_LONG` (`qe-canary-xrp-dry-001`, `sl_price=0.9187`, `tp_price=0.9561`, `margin_vst=100.0`, `leverage=5`) -> `status=SUBMITTED`, `exchange_order_id=paper-1`, `error=null`;
- `qe_harness_cli.py context --symbol BTCUSDT --out runtime/canary_ctx_BTCUSDT.json` -> `last_price = 60164.1`;
- `qe_harness_cli.py execute --live-vst` для `BTCUSDT OPEN_LONG` (`qe-canary-btc-open-001`, `100 VST`, `5x`, `sl_price=59562.5`, `tp_price=61066.6`);
- `qe_harness_cli.py execute --live-vst` для мінімального `BTCUSDT CLOSE_LONG` (`qe-canary-btc-close-001`, без полів `margin_vst` та `leverage`).

## 3. Верифікація SL/TP і мінімальної схеми CLOSE_* на BingX VST
1. **Відкриття позиції (`qe-canary-btc-open-001`):**
   - `client_order_id`: `qe-btcusdt-ol-864cab67829c8d4e`
   - `exchange_order_id`: `2125720090734235648`
   - `status`: `FILLED`, `filled_qty`: `0.0083`, `avg_price`: `60188.4`
2. **Негайна перевірка `qe_harness_cli.py status` на BingX VST:**
   - Відкрита позиція: `BTCUSDT`, `positionSide=LONG`, `contracts=0.0083`, `entry_price=60188.4`, `margin_mode=isolated`.
   - **TP-ордер (`2125720093496795136`):** `rawType="TAKE_PROFIT_MARKET"`, `workingType="MARK_PRICE"`, `side="sell"`, `positionSide="LONG"`, `reduceOnly=true`, `triggerPrice=61066.6`, `stopPrice=61066.6` (відхилення від запрошеного `61066.6`: **0.0**).
   - **SL-ордер (`2125720093496795137`):** `rawType="STOP_MARKET"`, `workingType="MARK_PRICE"`, `side="sell"`, `positionSide="LONG"`, `reduceOnly=true`, `triggerPrice=59562.5`, `stopPrice=59562.5` (відхилення від запрошеного `59562.5`: **0.0**).
3. **Закриття мінімальною схемою (`qe-canary-btc-close-001`) без `margin_vst` і `leverage`:**
   - `client_order_id`: `qe-btcusdt-cl-670bff742690321e`
   - `exchange_order_id`: `2125720431152336896`
   - `status`: `FILLED`, `filled_qty`: `0.0083`, `avg_price`: `60198.7`, `reduceOnly=true` (у журналі `margin_vst=0.0`, `leverage=1`).
4. **Фінальний статус біржі:**
   - `open_positions_count = 0` (`FLAT`), `open_orders_count = 0` (`0` orphan-ордерів, BingX автоматично зняв парні SL/TP після повного закриття `LONG`).
   - Баланс: `261042.0767 VST -> 261041.6625 VST` (`ESTIMATED_CANARY_PNL_VST = -0.4142 VST`, комісії ринкового входу/виходу з урахуванням цінового руху `+10.3 USDT/BTC` на `0.0083 BTC`).

## 4. Телеметрія проксі та журналу
- **Журнал `runtime/lean_trade_journal.jsonl`:** додано 3 коректні записи (`qe-canary-xrp-dry-001` `SUBMITTED`, `qe-canary-btc-open-001` `FILLED`, `qe-canary-btc-close-001` `FILLED`), усіх із `error: null`.
- **Лог проксі `runtime/agy_openai_proxy.log` (`gemini-4-carbon-medium`, `stream=true`):**
  - Всього викликів: **7**, усі `status: "ok"`.
  - `BrokenPipeError` / `client_disconnected`: **0**.
  - Затримки по ходах: `22.06s`, `18.03s`, `25.14s`, `27.10s`, `28.69s`, `17.20s`, **`32.85s`** (ход понад 30s успішно пройшов завдяки SSE keepalive та таймауту `180s` без обриву з'єднання).

## 5. Операційний Runbook
1. Переконатися, що проксі працює на `127.0.0.1:8045`:
   `curl -sf http://127.0.0.1:8045/health`
2. Запуск ізольованого Hermes через Antigravity Bridge:
   `/home/layman/QuantumEdge/tools/run_hermes_via_antigravity.sh --base-url http://127.0.0.1:8045/v1 --model gemini-4-carbon-medium`
3. Синхронізація звіту в Google Drive:
   `/home/layman/QuantumEdge/tools/sync_report_to_gdrive.sh /home/layman/QuantumEdge/reports/tasks/QE_HERMES_SL_TP_AND_PROXY_CANARY_V1.md`

## 6. Ризики та готовність до тривалої сесії
- Усі 4 виправлення з `QE_D2_09` (`0fd564f`) підтверджено на живому BingX VST без жодного дефекту (`DETECTED_ISSUES_FOR_CODER=NONE`).
- **Інформаційне спостереження:** на акаунті BingX VST для `BTCUSDT` встановлене плече біржі `10x` (`isolated`), тоді як розмір позиції розраховується `LeanExecutor` від `margin_vst * decision.leverage` (`100 * 5 = 500 VST` -> `0.0083 BTC`), оскільки адаптер свідомо не мутує налаштування плеча акаунта (`set_leverage`). Ризик фактичного номіналу залишається строго обмеженим `500 VST`.
- Контур повністю готовий до переходу на наступний гейт (`START_BOUNDED_1H_AUTONOMOUS_VST_SESSION_OR_BUILD_LOCAL_ML_LAYER`).
