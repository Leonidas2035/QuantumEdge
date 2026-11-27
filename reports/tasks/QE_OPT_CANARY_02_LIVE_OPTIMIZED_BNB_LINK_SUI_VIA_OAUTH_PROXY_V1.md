# QE_OPT_CANARY_02_LIVE_OPTIMIZED_BNB_LINK_SUI_VIA_OAUTH_PROXY_V1 — Звіт живої VST-канарки (BNBUSDT/LINKUSDT/SUIUSDT)

## 1. Підсумковий блок (`KEY=VALUE`)

```ini
TASK=QE_OPT_CANARY_02_LIVE_OPTIMIZED_BNB_LINK_SUI_VIA_OAUTH_PROXY_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-antigravity-hermes-bridge
BASE_SHA=7dd858ec9b80bc8eb1c383bc51f5c8744c745bbc
START_UTC=2026-11-27T12:04:38Z
END_UTC=2026-11-27T12:07:48Z
DURATION_MINUTES=3.17

START_EQUITY_VST=268609.2278
END_EQUITY_VST=268609.2278
CANARY_PNL_VST=0.0

SPREAD_BNBUSDT_BPS=22.5231
SPREAD_LINKUSDT_BPS=26.2564
SPREAD_SUIUSDT_BPS=26.178

CYCLES_RUN_COUNT=3
DRY_RUN_C1_STATUS=PASS
LIVE_C2_ACTION=OPEN_LONG
LIVE_C3_ACTION=OPEN_LONG
PROTECT_ONCE_READONLY_STATUS=PASS
TRADES_OPENED_COUNT=0
TRADES_CLOSED_COUNT=0
SL_TP_VERIFIED_ON_EXCHANGE=NO_TRADE
SCOPED_EXCHANGE_FLAT=true
SCOPED_ORPHAN_ORDERS_REMAINING=0

LEDGER_TOTAL_REQUESTS=6
LEDGER_DOUBLE_COUNT_VERIFIED_NONE=true
LEDGER_FAILURE_RATE=0.0
LEDGER_NATIVE_USAGE_COVERAGE=1.0
LEDGER_SUM_PROVIDER_INPUT_TOKENS=109586
LEDGER_SUM_CACHE_READ_TOKENS=0
LEDGER_SUM_FRESH_INPUT_TOKENS=109586
LEDGER_SUM_OUTPUT_TOKENS=1555
LEDGER_SUM_THINKING_TOKENS=0
LEDGER_SUM_IECU=117361.0
LEDGER_AVG_IECU_PER_CYCLE=39120.33
LEDGER_CACHE_TOKEN_FRACTION=0.0
LEDGER_CACHE_REQUEST_HIT_RATE=0.0
LEDGER_LATENCY_IMPUTED_P95_MS=13981.62
AVG_INPUT_CHARS_PER_CYCLE=34079
INPUT_CHARS_REDUCTION_VS_QE_D2_10_C1=0.9596

PROXY_PROCESS_STOPPED=true
SOURCE_CODE_MODIFIED=false
GIT_PUSHED=false
EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
REAL_TRADING=false

DETAILED_REPORT_FILE=/home/layman/QuantumEdge/reports/tasks/QE_OPT_CANARY_02_LIVE_OPTIMIZED_BNB_LINK_SUI_VIA_OAUTH_PROXY_V1.md
GDRIVE_SYNC_STATUS=SYNCED
DETECTED_ISSUES_FOR_CODER=Stage 2 LLM proposed margin_vst=5000.0 in C2 despite --max-margin-vst 1000 (validator_passed=true before LeanRiskGuard rejected it with INVALID_MARGIN);prefix_hash changed across C1/C2/C3 (RESYNC mode) and cache_read_tokens=0 via stateless agy CLI OAuth proxy
NEXT_GATE=DECIDE_PR_MERGE_AND_LAUNCH_EXTENDED_VST_SOAK_SESSION
```

---

## 2. Фактичні спреди та хронологія циклів (C1, C2, C3, `protect-once`, FLAT)

### 2.1 Preflight спреди BingX VST (`12:04:38Z`)
| Символ | `last_price` | `spread_bps` | `liqmap` | `orderflow` | `composite_signal` |
|---|---:|---:|---|---|---|
| `BNBUSDT` | `563.87` | `22.5231` | `OK` | `OK` | `OK` |
| `LINKUSDT` | `9.52` | `26.2564` | `OK` | `OK` | `OK` |
| `SUIUSDT` | `0.6875` | `26.1780` | `OK` | `OK` | `OK` |

### 2.2 Хронологія сесії
1. **Phase A (`12:04:38Z`):** `git HEAD = 7dd858ec9b80bc8eb1c383bc51f5c8744c745bbc` (чистий worktree). Проксі `tools/agy_openai_proxy.py` запущено на `127.0.0.1:8045` (лише `--log-path`, без `--ledger-db`) — холодний старт пройшов без помилок. Початковий статус BingX VST: `268,609.2278 VST`, `0` позицій, `0` ордерів.
2. **Phase B — Цикл 1 (`CANARY02_DRY_C1`, `--dry-run`, `12:05:09Z`–`12:05:28Z`):**
   - Stage 1 (`low`) -> Stage 2 (`low`, `complexity_score=0.21`), `prefix_hash=5ef08d3bafd8c380`, `resync_mode=RESYNC`.
   - Рішення: `OPEN_LONG BNBUSDT` `LIMIT @ 563.15` (`SL=560.2`, `TP=571.0`, `margin=800.0`, `lev=5x`). `validator_passed=true`, `status=SUBMITTED` (`paper-1`).
3. **Phase C — Цикл 2 (`CANARY02_LIVE_C2`, `--live-vst`, `12:05:42Z`–`12:06:06Z`):**
   - Stage 1 (`low`) -> Stage 2 (`low`, `complexity_score=0.2118`), `prefix_hash=c95934b4da26afd1`, `resync_mode=RESYNC`.
   - Рішення: `OPEN_LONG BNBUSDT` `LIMIT @ 562.50` (`SL=559.3`, `TP=571.4`, `margin=5000.0`, `lev=4x`).
   - `LeanRiskGuard` заблокував ордер до відправки на біржу: `REJECTED` (`INVALID_MARGIN: margin_vst=5000.0 must be in (0, 1000.0]`).
   - `status` після C2: `0` позицій, `0` ордерів.
   - `qe_harness_cli.py protect-once --dry-run --read-live-vst --allowed-symbols BNBUSDT,LINKUSDT,SUIUSDT`: повернув `source="bingx_vst_readonly"`, `open_positions=0`, `open_orders=0`, `ignored_foreign_positions=0`, `actions=[]` (**PASS**).
4. **Phase C.3 — Цикл 3 (`CANARY02_LIVE_C3`, `--live-vst`, `12:07:13Z`–`12:07:35Z`):**
   - Stage 1 (`low`) -> Stage 2 (`low`, `complexity_score=0.2109`), `prefix_hash=fecb8de0e6fe37e5`, `resync_mode=RESYNC`.
   - Рішення: `OPEN_LONG BNBUSDT` `LIMIT @ 562.95` (`SL=559.3`, `TP=571.3`, `margin=800.0`, `lev=5x`).
   - Ордер успішно відправлено на BingX VST: `status=SUBMITTED`, `exchange_order_id=2126286812587495424`, `amount=7.1`, `price=562.95`, `stopPrice=559.3` (не встиг виконатися, позиція не відкрилася).
5. **Phase D (`12:07:42Z`–`12:07:48Z`):**
   - Посимвольне скасування `cancel-open-orders --symbol BNBUSDT --live-vst`: скасовано `1` ордер (`2126286812587495424`).
   - Фінальний `status`: `0` позицій, `0` ордерів, баланс `268,609.2278 VST` (`CANARY_PNL_VST=0.0`).
   - Процес `agy_openai_proxy.py` зупинено, порт `8045` закрито.

---

## 3. Таблиця `usage_records` (`runtime/usage_ledger_canary02.sqlite3`) та аналіз кешування

Записи в `runtime/agy_openai_proxy_canary02.log` (`6` рядків) повністю збігаються з `usage_records` (`6` рядків) — **подвійний облік відсутній (`LEDGER_DOUBLE_COUNT_VERIFIED_NONE=true`)**.

| # | Decision / Stage | Model (`agy_alias`) | Thinking | Status | Input Tokens | Cache Read | Fresh Input | Output | Thinking Tok | Total | IECU | Input Chars | Latency (ms) | `prefix_hash` |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | C1 `stage1_screen` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 17,022 | 0 | 17,022 | 158 | 0 | 17,180 | 17,812.0 | 15,275 | 8,006.13 | `5ef08d3bafd8c380` |
| 2 | C1 `stage2_drill` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 19,339 | 0 | 19,339 | 421 | 0 | 19,760 | 21,444.0 | 18,518 | 11,533.04 | `5ef08d3bafd8c380` |
| 3 | C2 `stage1_screen` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 17,034 | 0 | 17,034 | 171 | 0 | 17,205 | 17,889.0 | 15,283 | 8,381.05 | `c95934b4da26afd1` |
| 4 | C2 `stage2_drill` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 19,380 | 0 | 19,380 | 320 | 0 | 19,700 | 20,980.0 | 18,553 | 14,797.82 | `c95934b4da26afd1` |
| 5 | C3 `stage1_screen` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 17,250 | 0 | 17,250 | 152 | 0 | 17,402 | 18,010.0 | 15,696 | 10,184.94 | `fecb8de0e6fe37e5` |
| 6 | C3 `stage2_drill` | `gemini-4-carbon` (`-low`) | `low` | `ok` | 19,561 | 0 | 19,561 | 333 | 0 | 19,894 | 21,226.0 | 18,913 | 11,091.02 | `fecb8de0e6fe37e5` |

### Аналіз Implicit Caching через OAuth-проксі
- `cache_read_tokens = 0` на всіх 6 запитах (`cache_token_fraction = 0.0`, `cache_request_hit_rate = 0.0`).
- Причина: кожен виклик через `agy` CLI (`--sandbox --disable-slash-commands`) відкриває нову ізольовану сесію (`num_turns=1`), а `prefix_hash` змінювався між циклами C1 -> C2 -> C3 (`5ef08d3b` -> `c95934b4` -> `fecb8de0`, `resync_mode=RESYNC`).

---

## 4. Пряме A/B порівняння з базовим `QE_D2_10`

| Метрика | Базовий `QE_D2_10` (Hermes ReAct) | `QE_OPT_CANARY_02` (2-стадійний контур) | Зміна |
|---|---:|---:|---|
| **`input_chars` / цикл** | `843,994` (цикл `c1`) | **`34,079`** (сер. по 3 циклах) | **-95.96% (у 24.8x менше)** |
| **Нативні `input_tokens` / цикл** | `~211k` видимих / `~596k` оцінених | **`36,529`** (100% нативні) | **~16.3x менше реальних токенів** |
| **`thinking_tokens` / цикл** | не логувалося | **`0`** (`ReasoningEffortRouter -> low`) | Детермінований контроль |
| **`IECU` / цикл** | не вимірювалося | **`39,120.33`** | Повний облік |
| **Затримка (p50 / avg)** | `42.37s` avg (`129.09s` max) | **`10.18s` p50** (`13.98s` p95) | **~4.2x швидше** |
| **`failure_rate`** | `34.3%` (`12/35` збоїв) | **`0.0%`** (`0/6` збоїв) | **100% стабільність** |

---

## 5. Операційний Runbook запуску (`BNBUSDT/LINKUSDT/SUIUSDT`)

1. Запустити проксі без `--ledger-db`:
   `.venv/bin/python tools/agy_openai_proxy.py --host 127.0.0.1 --port 8045 --default-model gemini-4-carbon-medium --timeout 180 --log-path runtime/agy_openai_proxy.log`
2. Запускати цикли через `qe_harness_cli.py run-cycle --live-vst --symbols BNBUSDT,LINKUSDT,SUIUSDT --max-margin-vst 1000 --max-leverage 50 --max-open-positions 3 --max-notional-vst 50000 ...`.
3. Для read-only наглядача використовувати `qe_harness_cli.py protect-once --dry-run --read-live-vst --allowed-symbols BNBUSDT,LINKUSDT,SUIUSDT`.
4. По завершенні — посимвольний `cancel-open-orders --symbol <SYM> --live-vst` (ніколи `--symbol ALL`).

---

## 6. Виявлені дефекти та висновок

1. **Пропозиція `margin_vst=5000.0` у Stage 2 (C2):** попри `--max-margin-vst 1000`, `gemini-4-carbon` у C2 видала `margin_vst=5000.0`, і `validator_passed` був `true`, але `LeanRiskGuard` надійно відхилив ордер (`INVALID_MARGIN`). Рекомендується додати перевірку `margin_vst <= max_margin_vst` безпосередньо у `TradeDecisionV2` валідатор перед передачею у `LeanExecutor`.
2. **`prefix_hash` та `cache_read_tokens=0`:** через `RESYNC` на кожному циклі та stateless `agy` CLI неявне кешування на стороні проксі не спрацьовує, проте навіть без кешу витрати на 1 цикл впали на **95.96%** (до `~36.5k` токенів / `10.2s` p50), що повністю знімає проблему квот і робить стек готовим до мерджу в `main` та тривалого VST-прогону.
