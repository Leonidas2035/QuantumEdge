# QE_D2_09_FIX_LIVE_VST_ISSUES_V1

## 1. KEY=VALUE
```text
TASK=QE_D2_09_FIX_LIVE_VST_ISSUES_V1
FINAL_VERDICT=PASS
BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=e11acd9b4ef9533b21fef6546242df0f5edf48ef
TARGET_BRANCH=feat/qe-antigravity-hermes-bridge
SL_TP_TRIGGER_PRICE_PARSING_FIXED=true
SL_TP_CCXT_BINGX_FORMAT_VERIFIED=true
PROXY_BROKEN_PIPE_AND_SSE_KEEPALIVE_FIXED=true
HERMES_CLIENT_TIMEOUT_CONFIGURED=true
XRPUSDT_ALLOWED_BY_DEFAULT=true
CLOSE_SCHEMA_MINIMAL_VERIFIED=true
PROVIDER_GETS=3
PROVIDER_MUTATIONS=0
```

## 2. Змінені файли та інтерфейси
- [`src/quantum_edge_core/execution/bingx_vst_adapter.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/execution/bingx_vst_adapter.py): у `fetch_vst_status()` додано послідовний fallback для тригерної ціни (`triggerPrice` -> `stopPrice` -> `stopLossPrice` -> `takeProfitPrice` -> `info.stopPrice` -> `info.StopPrice` -> `info.triggerPrice`), а також поля `triggerPrice`, `stopPrice`, `rawType` (`info.type`), `workingType` (`info.workingType`), `reduceOnly`, `positionSide`, `price`.
- [`src/quantum_edge_core/execution/lean_executor.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/execution/lean_executor.py): підтверджено сумісність `{"triggerPrice": float(...)}` із `ccxt.bingx.create_order_request()` (автоматично формує `STOP_MARKET` / `TAKE_PROFIT_MARKET` з `workingType="MARK_PRICE"` і забороняє SL/TP на `CLOSE_*`) та безпечний fallback `leverage` (`decision.leverage or 1`).
- [`tools/agy_openai_proxy.py`](file:///home/layman/QuantumEdge/tools/agy_openai_proxy.py): додано `_safe_write()` із перехопленням `BrokenPipeError`/`ConnectionResetError` та логуванням `client_disconnected`, фоновий worker-потік для `stream=true` із SSE keepalive (`: keepalive\n\n` кожні 10s) і підвищено `DEFAULT_TIMEOUT_SEC` до `180`.
- [`tools/run_hermes_via_antigravity.sh`](file:///home/layman/QuantumEdge/tools/run_hermes_via_antigravity.sh): додано `providers.custom.request_timeout_seconds: 180` (`stale_timeout_seconds: 300`) у `config.yaml` та `HERMES_API_TIMEOUT=180` у `.env` ізольованого `HERMES_HOME`.
- [`src/quantum_edge_core/execution/risk_guard.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/execution/risk_guard.py) і [`tools/qe_harness_cli.py`](file:///home/layman/QuantumEdge/tools/qe_harness_cli.py): додано `"XRPUSDT"` до `RiskConfig.allowed_symbols`, а також прапорець `--allowed-symbols` (і змінну середовища `QE_ALLOWED_SYMBOLS`).
- [`src/quantum_edge_core/contracts/lean.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/contracts/lean.py): `TradeDecision.leverage` змінено на `int | None = 1` (разом із `margin_vst: float = 0.0`), що дозволяє мінімальний JSON для `CLOSE_LONG` / `CLOSE_SHORT` / `HOLD`.
- [`tests/test_live_vst_fixes.py`](file:///home/layman/QuantumEdge/tests/test_live_vst_fixes.py): додано 6 офлайн-тестів регресії для всіх 4 дефектів.

## 3. Архітектурні рішення та компроміси
- **Чому `status` показував `stopPrice=null`:** інспекція `ccxt.bingx.parse_order()` у `.venv` показала, що CCXT переносить `info.stopPrice` у `stopLossPrice`/`takeProfitPrice` і навмисно скидає `triggerPrice=None`. Замість модифікації CCXT додано детермінований fallback у `bingx_vst_adapter.py`, що зберігає сумісність із будь-якими версіями CCXT.
- **Чому залишено `{"triggerPrice": float(...)}` у `LeanExecutor`:** `ccxt.bingx.create_order_request()` сам розгортає цей словник у `{"stopPrice": ..., "workingType": "MARK_PRICE", "type": "STOP_MARKET"|"TAKE_PROFIT_MARKET"}`, що підтверджено офлайн-викликом `create_order_request()` без мережі та не ламає наявні тести.
- **Захист від обриву на 34-й секунді:** замість блокуючого очікування в HTTP-потоці, streaming-гілка `agy_openai_proxy.py` запускає `agy` у worker-потоці й надсилає `: keepalive\n\n` кожні 10s, а будь-який `BrokenPipeError` безпечно фіксується як `client_disconnected`.

## 4. Телеметрія та докази тестів
- `compileall` по `src/quantum_edge_core/contracts`, `src/quantum_edge_core/execution`, `tools/qe_harness_cli.py`, `tools/agy_openai_proxy.py`: `PASS`.
- Повний офлайн-набір (`tests/test_live_vst_fixes.py`, `tests/test_sync_report_to_gdrive.py`, `tests/test_agy_openai_proxy.py`, `tests/test_antigravity_bridge_verifier.py`, `tests/test_bingx_vst_harness_cli.py`, `tests/test_full_context_and_bridge.py`, `tests/test_lean_execution_core.py`, `tests/test_bingx_intent_and_safety.py`, `tests/test_bingx_vst.py`): `54 passed, 1 skipped, 0 failed`.
- Read-only `qe_harness_cli.py status`: `sandbox=true`, `balance_vst.free=261042.0767`, `open_positions_count=0`, `open_orders_count=0`, `provider_gets=3`, `provider_mutations=0`.

## 5. Операційний Runbook
1. Запуск проксі: `.venv/bin/python tools/agy_openai_proxy.py --host 127.0.0.1 --port 8045 --default-model gemini-4-carbon-medium --timeout 180`
2. Перевірка стану VST (без мутацій): `.venv/bin/python tools/qe_harness_cli.py status`
3. Синхронізація звіту на Google Drive (після OAuth `rclone config`): `tools/sync_report_to_gdrive.sh reports/tasks/QE_D2_09_FIX_LIVE_VST_ISSUES_V1.md`

## 6. Відкриті ризики та рекомендація
- `rclone` на `ARTLINE-PC` ще не авторизований оператором (`GDRIVE_SYNC_STATUS=NEEDS_OPERATOR_OAUTH`), тому синхронізація на Drive очікує виконання `rclone config`.
- Рекомендований наступний крок: короткий canary-трейд на BingX VST з перевіркою `qe_harness_cli.py status` одразу після `OPEN_*`, щоб наочно підтвердити `triggerPrice` і `rawType` (`STOP_MARKET` / `TAKE_PROFIT_MARKET`) у живому sandbox-відгуку.
