# QE_HERMES_15MIN_LIVE_VST_TEST_V1 — Звіт 15-хвилинного бойового VST-тесту

## 1. Підсумкові метрики (`KEY=VALUE`)

```ini
TASK=QE_HERMES_15MIN_LIVE_VST_TEST_V1
FINAL_VERDICT=PARTIAL

START_UTC=2026-11-25T15:35:12Z
END_UTC=2026-11-25T15:50:31Z
DURATION_MINUTES=15.32

START_EQUITY_VST=261054.441
END_EQUITY_VST=261042.0767
ESTIMATED_SESSION_PNL_VST=-12.3643

PAIRS_ANALYZED=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT
TRADES_OPENED_COUNT=1
TRADES_CLOSED_COUNT=1
SL_TP_VERIFIED_ON_EXCHANGE=false
REDUCE_ONLY_CLOSE_VERIFIED=true
FINAL_EXCHANGE_FLAT=true
ORPHAN_ORDERS_REMAINING=0

JOURNAL_ENTRIES_LOGGED=2
LEDGER_DUPLICATE_OR_UNKNOWN_EVENTS=0
PROXY_CALLS_COUNT=2
PROXY_AVG_LATENCY_SEC=21.40
CLI_OR_EXCHANGE_ERRORS_COUNT=1

DETAILED_REPORT_FILE=/home/layman/QuantumEdge/reports/tasks/QE_HERMES_15MIN_LIVE_VST_TEST_V1.md
SOURCE_CODE_MODIFIED=false
EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
REAL_TRADING=false

DETECTED_ISSUES_FOR_CODER=SL/TP triggerPrice/stopPrice omitted in qe_harness_cli.py status (returns stopPrice=null and price=entry_price);agy_openai_proxy 34s non-streaming call on gemini-4-carbon-medium hit Broken pipe;LeanRiskGuard default allowed_symbols excludes XRPUSDT;BingX VST altcoin spreads ~25-27 bps vs BTC 0.34 bps;CLOSE_LONG schema requires margin_vst/leverage fields
NEXT_GATE=FIX_DETECTED_LIVE_VST_ISSUES_IN_QUANTUMEDGE
```

---

## 2. Хронологія подій (`T+0 .. T+15 хв`)

| Час (UTC) | Фаза | Дія / Команда | Деталі та результат |
|---|---|---|---|
| `15:35:12Z` | **Phase A** (`T+0m`) | `qe_harness_cli.py status` | `sandbox: true`, баланс `261054.441 VST` (`used: 0.0`), `open_positions_count: 0`, `open_orders_count: 0`. |
| `15:35:17Z – 15:35:28Z` | **Phase A** (`T+0m`) | `qe_harness_cli.py context` для `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT` | Зібрано 5 ТФ (`1m/5m/15m/1h/4h`, по 100 барів) + L2 стакан + деривативи. Спреди: `BTCUSDT` **0.34 bps**, `ETHUSDT` **26.68 bps**, `SOLUSDT` **25.04 bps**, `XRPUSDT` **24.54 bps**. `risk_limits.allowed_symbols` = `['BTCUSDT', 'ETHUSDT', 'SOLUSDT']`. |
| `15:35:53Z` | **Phase B** (`T+0.7m`) | `execute --live-vst` (`qe-15m-open-001`, `OPEN_LONG BTCUSDT`, `MARKET`, `margin_vst=500`, `leverage=10`, `sl=57700.0`, `tp=59100.0`) | `FILLED`: `exchange_order_id=2125614467564703744`, `client_order_id=qe-btcusdt-ol-e78f475660b91163`, `filled_qty=0.0857`, `avg_price=58317.6`. |
| `15:35:55Z` | **Phase B** (`T+0.7m`) | `status` (перевірка після відкриття) | Позиція `BTCUSDT LONG` (`0.0857`, `entry=58317.5`, `used=499.7818 VST`, `uPnL=-0.3943`). Відкрито `2` ордери `reduceOnly=true` (`2125614470272737280`, `2125614470272737281`), але обидва мають `stopPrice: null` і `price: 58317.6`. |
| `15:39:11Z` | **Phase C** (`T+4m`) | `status` + `context BTCUSDT` | `mark_price=58393.1`, `uPnL=+6.4705 VST`, `spread=0.2227 bps`, обидва SL/TP ордери `open`. |
| `15:42:22Z` | **Phase C** (`T+7.2m`) | `status` + `context BTCUSDT` | `mark_price=58320.8`, `uPnL=+0.2743 VST`, `spread=0.2229 bps`, обидва SL/TP ордери `open`. |
| `15:45:34Z` | **Phase C** (`T+10.4m`) | `status` + `context BTCUSDT` | `mark_price=57988.9`, `uPnL=-28.1695 VST`, `spread=0.4483 bps`, SL (`57700`) не спрацював, ордери `open`. |
| `15:48:45Z` | **Phase C** (`T+13.5m`) | `status` | `mark_price=57985.1`, `uPnL=-28.4944 VST`, обидва ордери `open`. |
| `15:50:28Z` | **Phase D** (`T+15.3m`) | `execute --live-vst` (`qe-15m-close-001`, `CLOSE_LONG BTCUSDT`, `MARKET`) | `FILLED`: `exchange_order_id=2125618134644690944`, `client_order_id=qe-btcusdt-cl-357d0775eb62b6c7`, `filled_qty=0.0857`, `avg_price=58231.6`. |
| `15:50:30Z – 15:50:31Z` | **Phase D** (`T+15.3m`) | `status` (фінальна перевірка) | `balance_vst.total=261042.0767` (`used: 0.0`), `open_positions_count=0` (**FLAT**), `open_orders_count=0` (**0 orphan orders**). |

---

## 3. Перевірка SL/TP та `reduceOnly` на BingX VST

1. **Поведінка SL/TP при відкритті (`OPEN_LONG`):**
   - `qe_harness_cli.py execute --live-vst` створив 2 захисні ордери (`2125614470272737280` та `2125614470272737281`) із прапорцями `type: "market"`, `side: "sell"`, `positionSide: "LONG"`, `reduceOnly: true`.
   - **Виявлений дефект діагностики:** `qe_harness_cli.py status` повернув `stopPrice: null` і `price: 58317.6` (ціна входу) для обох ордерів, через що неможливо підтвердити через CLI, чи закріпилися фактичні рівні `sl_price=57700.0` і `tp_price=59100.0` на біржі (імовірно, `status` не витягує `info.stopPrice` / `triggerPrice` з відповіді CCXT BingX, або ордери виставлено некоректно). Через це `SL_TP_VERIFIED_ON_EXCHANGE=false`, а загальний вердикт — `PARTIAL`.
2. **Закриття `CLOSE_LONG` (`reduceOnly=True`) та очищення orphan-ордерів:**
   - `CLOSE_LONG` закрив рівно `0.0857 BTC` у `FLAT` без відкриття зустрічного `SHORT`.
   - Біржа BingX VST автоматично скасувала обидва прикріплені `reduceOnly` ордери (`open_orders_count=0` одразу після закриття).

---

## 4. Телеметрія журналу та Agy OpenAI Proxy

- **[lean_trade_journal.jsonl](file:///home/layman/QuantumEdge/runtime/lean_trade_journal.jsonl):** містить `3` записи загалом, з них `2` за поточний 15-хвилинний тест (`qe-15m-open-001` та `qe-15m-close-001`, обидва `FILLED`, `error: null`, дублікатів чи невідомих подій `0`).
- **[agy_openai_proxy.log](file:///home/layman/QuantumEdge/runtime/agy_openai_proxy.log):** усього `9` рядків (`6` попередніх запитів на `gemini-4-hydrogen-medium` о `15:04–15:05Z` зі `status: "ok"` та `3` рядки на `gemini-4-carbon-medium` о `15:34Z`):
  - `gemini-4-carbon-medium` (`15:34:23Z` та `15:34:57Z`): `2` успішні виклики (`8800.38 ms`, `tool_calls_count=1`; та `34000.91 ms`, `tool_calls_count=0`; середня затримка `21.40s`), після чого зафіксовано **`[Errno 32] Broken pipe`** о `15:34:57.740Z` (ймовірно, клієнтський таймаут ~30–34s на нестрімінговому запиті).

---

## 5. Список виявлених дефектів і рекомендацій для кодера

1. **[HIGH] Непрозорість SL/TP у `qe_harness_cli.py status`:** для захисних ордерів BingX VST виводиться `stopPrice: null` і `price: <entry_price>`. Потрібно розпарсити `order["triggerPrice"]` / `order["info"]["stopPrice"]` / `order["info"]["triggerPrice"]` та `order["info"]["workingType"]` у `status`, щоб гарантовано верифікувати `SL`/`TP` на біржі.
2. **[MEDIUM] Таймаут / `Broken pipe` у `agy_openai_proxy` при `stream=false` (`34s`):** налаштувати keep-alive / збільшити клієнтський read-timeout або віддавати перевагу `stream=true` для `gemini-4-carbon-medium`, щоб уникати обриву з'єднання після 30+ секунд очікування.
3. **[INFO] `LeanRiskGuard` `allowed_symbols`:** за замовчуванням дозволяє лише `BTCUSDT, ETHUSDT, SOLUSDT` і виключає `XRPUSDT` (хоча `context --symbol XRPUSDT` працює).
4. **[INFO] Схема `TradeDecision` для `CLOSE_*`:** вимагає передачі `margin_vst` та `leverage` навіть при повному закритті існуючої позиції по `CLOSE_LONG`/`CLOSE_SHORT`.
5. **[INFO] Спреди на BingX VST:** альткоїни (`ETHUSDT` `26.7 bps`, `SOLUSDT` `25.0 bps`, `XRPUSDT` `24.5 bps`) мають суттєво ширші спреди, ніж `BTCUSDT` (`0.22–0.45 bps`).
