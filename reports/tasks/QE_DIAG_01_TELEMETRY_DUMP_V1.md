# QE_DIAG_01_TELEMETRY_DUMP_V1 — Повна телеметрія сесії QE_D2_10 та матриця моделей

## 1. Підсумки звірки Git HEAD та процесів (Phase A)

- **Branch:** `feat/qe-antigravity-hermes-bridge`
- **ACTUAL_HEAD_SHA:** `c9de1b0cf40327b81ff8feb9ecc927a5163099f9`
- **Пояснення розбіжності SHA (`c9de1b0c` vs `e7a4f8fa`):**
  У файлі `reports/tasks/QE_D2_10_ANALYTICS_ENRICHMENT_AND_1H_VST_SESSION_V1.md` (рядок 13) зафіксовано `FINAL_SHA=e7a4f8fa7bbd88d82c712a0cf54459cbd3e4c0e1`, оскільки перед фінальним `git commit --amend` скрипт замінив старий SHA на `e7a4f8fa...`, після чого сам `git commit --amend` згенерував новий фінальний хеш коміту `c9de1b0cf40327b81ff8feb9ecc927a5163099f9`. Обидва значення належать одному й тому самому архівному коміту до/після `amend`.
- **Активні процеси проксі/Hermes:** `0` (`NO_PROXY_PROCESSES`)

### `git log -n 5 --oneline --decorate`
```text
c9de1b0 (HEAD -> feat/qe-antigravity-hermes-bridge) docs(reports): archive QE_D2_10 analytics enrichment and 1h vst session report
7b8203d feat(analytics): integrate liqmap, orderflow, and composite_signal into FullMarketContext and archive canary report
0fd564f fix(execution,bridge): parse BingX SL/TP triggerPrice, harden proxy timeout/SSE, allow XRPUSDT, simplify CLOSE schema
e11acd9 feat(infra): add Google Drive rclone sync helper and mount readiness for task reports
39dafb1 feat(bridge): add local Agy CLI OpenAI-compatible proxy with tool calling and E2E dry-run verification
```

---

## 2. Повна розбивка `runtime/agy_openai_proxy.log` (Phase B.1)

- **Всього рядків:** `51`
- **Групування по моделях і статусах:**
  - `gemini-4-hydrogen-medium` (`ok`): `6`
  - `gemini-4-carbon-medium` (`ok`): `32`
  - `unknown` (`error`): `12`
  - `unknown` (`client_disconnected`): `1`
- **Step 2 (`ts_utc >= 2026-11-25T23:00`):**
  - Успішних (`ok`): `23` (усі на `gemini-4-carbon-medium`, `max input_chars = 207,312`)
  - Помилок/обривів: `12` (`print_timeout_3m0s`: 7, `empty_conversation_id`: 3, `quota_reached`: 1, `client_disconnected`: 1)

### Таблиця всіх 51 викликів проксі

| # | Timestamp (UTC) | Model | Status | Input Chars | Output Chars | Tool Calls | Latency (ms) | Error / Context |
|---:|---|---|---|---:|---:|---:|---:|---|
| 1 | `2026-11-25T15:04:32` | `gemini-4-hydrogen-medium` | `ok` | 1348 | 105 | 1 | 6908.71 | - |
| 2 | `2026-11-25T15:04:38` | `gemini-4-hydrogen-medium` | `ok` | 1579 | 128 | 0 | 6378.92 | - |
| 3 | `2026-11-25T15:04:52` | `gemini-4-hydrogen-medium` | `ok` | 13764 | 380 | 1 | 12446.09 | - |
| 4 | `2026-11-25T15:05:03` | `gemini-4-hydrogen-medium` | `ok` | 14392 | 542 | 1 | 8983.11 | - |
| 5 | `2026-11-25T15:05:18` | `gemini-4-hydrogen-medium` | `ok` | 15003 | 1096 | 1 | 10000.54 | - |
| 6 | `2026-11-25T15:05:37` | `gemini-4-hydrogen-medium` | `ok` | 15816 | 793 | 0 | 18768.87 | - |
| 7 | `2026-11-25T15:34:23` | `gemini-4-carbon-medium` | `ok` | 1348 | 105 | 1 | 8800.38 | - |
| 8 | `2026-11-25T15:34:57` | `gemini-4-carbon-medium` | `ok` | 1579 | 116 | 0 | 34000.91 | - |
| 9 | `2026-11-25T15:34:57` | `-` | `error` | - | - | - | - | `[Errno 32] Broken pipe` |
| 10 | `2026-11-25T22:33:46` | `gemini-4-carbon-medium` | `ok` | 15668 | 513 | 2 | 22058.46 | - |
| 11 | `2026-11-25T22:34:09` | `gemini-4-carbon-medium` | `ok` | 18048 | 525 | 1 | 18034.09 | - |
| 12 | `2026-11-25T22:34:34` | `gemini-4-carbon-medium` | `ok` | 19041 | 315 | 1 | 25140.24 | - |
| 13 | `2026-11-25T22:35:01` | `gemini-4-carbon-medium` | `ok` | 19686 | 1079 | 2 | 27100.95 | - |
| 14 | `2026-11-25T22:35:36` | `gemini-4-carbon-medium` | `ok` | 21606 | 746 | 1 | 28693.19 | - |
| 15 | `2026-11-25T22:35:57` | `gemini-4-carbon-medium` | `ok` | 24535 | 674 | 1 | 17199.53 | - |
| 16 | `2026-11-25T22:37:34` | `gemini-4-carbon-medium` | `ok` | 25830 | 3143 | 0 | 32849.55 | - |
| 17 | `2026-11-25T23:04:28` | `gemini-4-carbon-medium` | `ok` | 1348 | 105 | 1 | 6556.15 | - |
| 18 | `2026-11-25T23:04:37` | `gemini-4-carbon-medium` | `ok` | 1579 | 113 | 0 | 8994.99 | - |
| 19 | `2026-11-25T23:06:49` | `gemini-4-carbon-medium` | `ok` | 73115 | 581 | 2 | 63314.27 | - |
| 20 | `2026-11-25T23:07:13` | `gemini-4-carbon-medium` | `ok` | 77147 | 738 | 2 | 21242.91 | - |
| 21 | `2026-11-25T23:08:05` | `gemini-4-carbon-medium` | `ok` | 91084 | 1822 | 4 | 47447.14 | - |
| 22 | `2026-11-25T23:08:56` | `gemini-4-carbon-medium` | `ok` | 133389 | 348 | 2 | 29891.07 | - |
| 23 | `2026-11-25T23:10:41` | `gemini-4-carbon-medium` | `ok` | 153929 | 943 | 1 | 103998.32 | - |
| 24 | `2026-11-25T23:11:33` | `gemini-4-carbon-medium` | `ok` | 157051 | 1004 | 1 | 45644.10 | - |
| 25 | `2026-11-25T23:12:18` | `gemini-4-carbon-medium` | `ok` | 158279 | 2581 | 0 | 45226.13 | - |
| 26 | `2026-11-25T23:17:26` | `gemini-4-carbon-medium` | `ok` | 72959 | 1814 | 1 | 103802.63 | - |
| 27 | `2026-11-25T23:17:30` | `-` | `error` | - | - | - | - | `failed to start conversation: backend returned an empty conversation id` |
| 28 | `2026-11-25T23:18:10` | `gemini-4-carbon-medium` | `ok` | 80034 | 1798 | 1 | 37143.08 | - |
| 29 | `2026-11-25T23:20:40` | `gemini-4-carbon-medium` | `ok` | 136889 | 460 | 3 | 129090.35 | - |
| 30 | `2026-11-25T23:21:01` | `gemini-4-carbon-medium` | `ok` | 151954 | 488 | 3 | 21492.47 | - |
| 31 | `2026-11-25T23:22:01` | `gemini-4-carbon-medium` | `ok` | 176475 | 475 | 3 | 59000.75 | - |
| 32 | `2026-11-25T23:22:20` | `gemini-4-carbon-medium` | `ok` | 193716 | 299 | 2 | 19101.48 | - |
| 33 | `2026-11-25T23:23:18` | `gemini-4-carbon-medium` | `ok` | 207312 | 299 | 2 | 58029.85 | - |
| 34 | `2026-11-25T23:23:20` | `-` | `error` | - | - | - | - | `failed to start conversation: backend returned an empty conversation id` |
| 35 | `2026-11-25T23:26:24` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 36 | `2026-11-25T23:29:31` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 37 | `2026-11-25T23:33:55` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 38 | `2026-11-25T23:34:00` | `-` | `error` | - | - | - | - | `failed to start conversation: backend returned an empty conversation id` |
| 39 | `2026-11-25T23:37:08` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 40 | `2026-11-25T23:37:32` | `gemini-4-carbon-medium` | `ok` | 1348 | 105 | 1 | 6873.04 | - |
| 41 | `2026-11-25T23:37:40` | `gemini-4-carbon-medium` | `ok` | 1579 | 116 | 0 | 7903.50 | - |
| 42 | `2026-11-25T23:40:10` | `gemini-4-carbon-medium` | `ok` | 20347 | 1722 | 1 | 94836.94 | - |
| 43 | `2026-11-25T23:40:40` | `gemini-4-carbon-medium` | `ok` | 22638 | 1678 | 1 | 29577.98 | - |
| 44 | `2026-11-25T23:41:08` | `gemini-4-carbon-medium` | `ok` | 25113 | 1185 | 0 | 25104.82 | - |
| 45 | `2026-11-25T23:47:23` | `gemini-4-carbon-medium` | `ok` | 20828 | 1699 | 1 | 84982.33 | - |
| 46 | `2026-11-25T23:47:25` | `-` | `error` | - | - | - | - | `Individual quota reached. Please upgrade your subscription... Resets in 2h49m46s.` |
| 47 | `2026-11-26T07:50:34` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 48 | `2026-11-26T07:53:38` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 49 | `2026-11-26T07:56:44` | `-` | `error` | - | - | - | - | `print timeout after 3m0s with the turn still in progress; the response is partial` |
| 50 | `2026-11-26T07:59:53` | `gemini-4-carbon-medium` | `ok` | 1348 | 105 | 1 | 92633.57 | - |
| 51 | `2026-11-26T07:59:53` | `-` | `client_disconnected` | - | - | - | - | `json_body` |

*(Примітка: проксі в поточній версії `tools/agy_openai_proxy.py` не логує `input_chars` та `latency_ms` у гілці обробки помилок).*

---

## 3. Журнал угод, нотатки та SQLite леджер (Phase B.2–B.3)

### `runtime/lean_trade_journal.jsonl` (всього 8 рядків; записи `QE_D2_10`)
- **`qe-1h-c1-btcusdt-open_long`** (`2026-11-25T23:10:43.365620+00:00`): `OPEN_LONG BTCUSDT` `MARKET`, `sl=59580.0`, `tp=60640.0`, `margin=900.0`, `lev=5`, `status=FILLED`, `client_order_id=qe-btcusdt-ol-ff2ec5f75aa6843d`, `exchange_order_id=2125728919685435392`.
- **`qe-1h-c4-btcusdt-hold`** (`2026-11-25T23:40:42.985955+00:00`): `HOLD BTCUSDT`, `status=SKIPPED_HOLD`, `client_order_id=qe-btcusdt-hd-7ce000155b7d17fe`.

### `runtime/session_1h_notes.jsonl` (2 записи)
- `c1` (`2026-11-25T23:10:49Z`): `BTCUSDT OPEN_LONG 0.075 @ 60146.0`, `sl_tp_verified=true`, `uPnL=0.4049`.
- `c4` (`2026-11-25T23:40:43Z`): `BTCUSDT HOLD`, `sl_tp_verified=true`, `uPnL=10.8001`.

### `runtime/lean_orders.sqlite3` (`orders` table: 7 рядків)
- За статусами: `FILLED`: 5, `SUBMITTED`: 2.
- Всі рядки (`decision_id`, `client_order_id`, `symbol`, `action`, `status`):
  1. `qe-smoke-agy-hermes-001` | `qe-btcusdt-ol-4810cb999e3f467c` | `BTCUSDT` | `OPEN_LONG` | `SUBMITTED`
  2. `qe-15m-open-001` | `qe-btcusdt-ol-e78f475660b91163` | `BTCUSDT` | `OPEN_LONG` | `FILLED`
  3. `qe-15m-close-001` | `qe-btcusdt-cl-357d0775eb62b6c7` | `BTCUSDT` | `CLOSE_LONG` | `FILLED`
  4. `qe-canary-xrp-dry-001` | `qe-xrpusdt-ol-ec3e99dda2085332` | `XRPUSDT` | `OPEN_LONG` | `SUBMITTED`
  5. `qe-canary-btc-open-001` | `qe-btcusdt-ol-864cab67829c8d4e` | `BTCUSDT` | `OPEN_LONG` | `FILLED`
  6. `qe-canary-btc-close-001` | `qe-btcusdt-cl-670bff742690321e` | `BTCUSDT` | `CLOSE_LONG` | `FILLED`
  7. `qe-1h-c1-btcusdt-open_long` | `qe-btcusdt-ol-ff2ec5f75aa6843d` | `BTCUSDT` | `OPEN_LONG` | `FILLED`

---

## 4. Розбивка розміру `runtime/step1_enriched_ctx_btcusdt.json` (Phase B.4)

- **Загальний розмір файлу:** `105,040` байт (неформатований/форматований JSON із відступами)

| Ключ верхнього рівня | Розмір (байти) | Примітка |
|---|---:|---|
| `ohlcv_by_tf` | **55,613** | Основне джерело обсягу (сирі свічки всіх таймфреймів) |
| `microstructure_and_liquidity` | **7,765** | `liqmap`: **6,659** B, `orderflow`: **599** B, `composite_signal`: **303** B, top10-метрики: 29 B |
| `orderbook` | **1,990** | Топ рівні книги заявок |
| `account_state` | **813** | Баланс, позиції, ордери |
| `market_snapshot` | **207** | Остання ціна, спред |
| `risk_limits` | **187** | Ліміти плеча/маржі |
| `derivatives` | **165** | Ставка фінансування |
| `raw_extras` | **70** | Додаткові поля |

---

## 5. Доступні моделі `agy`, прапорці та поля `usage` (Phase C)

### 5.1 Доступні моделі (`agy models`)
- **Легші / швидші моделі:**
  - `gemini-4-hydrogen-low`, `gemini-4-hydrogen-medium`, `gemini-4-hydrogen-high`, `gemini-4-hydrogen-xhigh`
  - `gemini-4-argon-low`, `gemini-4-argon-medium`, `gemini-4-argon-high`
  - `gemini-3.8-flash-low`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-high`
  - `gpt-oss-120b-medium`
- **Важчі / інші моделі:**
  - `gemini-4-carbon-low`, `gemini-4-carbon-medium`, `gemini-4-carbon-high`
  - `gemini-4-neon-low`, `gemini-4-neon-medium`, `gemini-4-neon-high`, `gemini-4-neon-xhigh`
  - `gemini-pro-orchestrator`, `gemini-pro-orchestrator-neon`, `gemini-pro-orchestrator-argon`
  - `claude-opus-5-5-low/medium/high`, `claude-sonnet-5-5-low/medium/high`

### 5.2 Прапорець відключення інструментів у `agy --help`
- Спеціального прапорця повного відключення внутрішніх інструментів (на кшталт `--no-tools` або `--disallowed-tools`) у `agy --help` **немає** (`NONE`).
- Наявні прапорці обмеження середовища: `--sandbox`, `--disable-slash-commands`, `--mode`, `--agent`, `--settings`.

### 5.3 Нативні поля `usage` у `agy --output-format json` та стан `agy_openai_proxy.py`
- Реальний вивід `agy --output-format json` повертає об'єкт `"usage"` із точними лічильниками токенів:
  - `input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens` (а також `num_turns` і `duration_seconds` на верхньому рівні).
- У `tools/agy_openai_proxy.py` (рядки 372–374) уже читаються `usage_raw.get("input_tokens")` та `usage_raw.get("output_tokens")` для OpenAI-відповіді клієнту, але метод `self.log_event({...})` (рядки 431–442) **не записує** ці поля у `runtime/agy_openai_proxy.log` (логуються лише `input_chars` та `output_chars`).
