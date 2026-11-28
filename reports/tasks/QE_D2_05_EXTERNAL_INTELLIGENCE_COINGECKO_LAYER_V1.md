# QE_D2_05_EXTERNAL_INTELLIGENCE_COINGECKO_LAYER_V1

## 1. Мета та Контекст (Stage QE-05)
Реалізовано шар зовнішньої ринкової розвідки (**External Intelligence Layer — CoinGecko Global Market Intelligence**) з потокобезпечним RAM TTL-кешем, fail-soft деградацією (`OK` -> `STALE` -> `UNAVAILABLE`), детермінованою класифікацією макро-режиму та інтеграцією у `P2` контекст оркестратора, сесійний ранер `run_vst_session.py` і TUI-бріфінг `qe_briefing.py`.

- **Гілка:** `feat/qe-d2-05-coingecko-intel` (відгалужено від `origin/feat/qe-d2-04-market-data-infra`, `BASE_SHA=b6eff48027fe13495481c3eeec1236b0e46a4ca9`)
- **Статус:** `COMPLETED` (100% offline-верифікація, zero live HTTP / BingX / OAuth / `agy` викликів)

## 2. Реалізовані компоненти та зміни

1. **Контракт `ExternalAdvisoryV1`**
   - [`src/quantum_edge_core/contracts/optimization.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/contracts/optimization.py): додано Pydantic-контракт `ExternalAdvisoryV1` (`source="coingecko"`, `status` in `OK|STALE|UNAVAILABLE`, `age_sec`, `total_market_cap_usd_b`, `market_cap_change_24h_pct`, `btc_dominance_pct`, `eth_dominance_pct`, `global_volume_24h_usd_b`, `macro_regime_tag` in `RISK_ON|NEUTRAL|RISK_OFF|ALT_ROTATION|BTC_DRAIN`, `asset_snapshots` з `ref_usd` та `bingx_basis_bps`, `trending_symbols`) і метод `to_compact_prompt_line()`, який формує компактний рядок `EXT_MACRO_V1|...` (<= 180 символів).
   - Реекспорт у [`src/quantum_edge_core/contracts/lean.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/contracts/lean.py) та [`src/quantum_edge_core/contracts/__init__.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/contracts/__init__.py).

2. **Клієнт `CoinGeckoIntelClient`**
   - [`src/quantum_edge_core/external_intel/__init__.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/external_intel/__init__.py) та [`src/quantum_edge_core/external_intel/coingecko_client.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/external_intel/coingecko_client.py):
     - `SYMBOL_TO_COINGECKO_ID` для всесвіту пар (`BNBUSDT`, `LINKUSDT`, `SUIUSDT`, `BTCUSDT`, `ETHUSDT`, `SOLUSDT`).
     - Потокобезпечний RAM TTL-кеш (`ttl_sec=600.0`, `stale_max_sec=3600.0`, `timeout_sec=8.0`).
     - Динамічний перерахунок `bingx_basis_bps` на кожному виклику навіть при cache hit.
     - Fail-soft fallback при HTTP 429 / мережевих збоях: повертає `STALE` в межах `stale_max_sec` або `UNAVAILABLE` після закінчення вікна, ніколи не кидає винятків у торговий цикл.
     - Підтримка `http_get_override` для 100% офлайн-тестування та лічильники `stats` (`http_calls`, `cache_hits`, `errors`, `stale_fallbacks`).

3. **Інтеграція в Оркестратор та Промпти (`P0` / `P2`)**
   - [`src/quantum_edge_core/orchestrator/prompts.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/orchestrator/prompts.py): додано правило `EXT_MACRO_V1` у `SYSTEM_PROMPT_P0_CORE` із збереженням бюджету префіксного кешу Gemini 4 (`P0 >= 4096` та `P0 + P1 <= 9500` SP токенів).
   - [`src/quantum_edge_core/orchestrator/deterministic_orchestrator.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/orchestrator/deterministic_orchestrator.py): `format_external_advisory_line()`, поле `PreFetchedBundle.external_advisory`, `PreFetchCoordinator(..., external_intel_client=...)`, проброс у `scoped_bundle` та інжекція в `P2` `StatelessStagePromptAssembler` (Stage 1 і Stage 2).
   - [`src/quantum_edge_core/orchestrator/prefix_context_assembler.py`](file:///home/layman/QuantumEdge/src/quantum_edge_core/orchestrator/prefix_context_assembler.py): інжекція `EXT_MACRO_V1` в `P2` для Stage 1 і Stage 2 без зміни `prefix_hash` (`P0+P1`).

4. **Сесійний ранер і TUI Бріфінг**
   - [`tools/run_vst_session.py`](file:///home/layman/QuantumEdge/tools/run_vst_session.py): прапорці `--enable-coingecko` / `--no-coingecko` (`default=None`: автоматично увімкнено на `--live-vst`, вимкнено на `--dry-run` / `--transport mock`) та `--coingecko-ttl-sec` (`default=600.0`), рядок телеметрії `EXT_INT | ...` у консолі, збереження `external_advisory` у `latest_session.json`, `CYCLE_COMPLETE` durable event log та Markdown-звіт.
   - [`tools/qe_briefing.py`](file:///home/layman/QuantumEdge/tools/qe_briefing.py): реконструкція `external_advisory` з JSON/event log та нова секція `## 4. Глобальний макро-контекст (CoinGecko)`.

## 3. Верифікація
- **Синтаксис та компіляція:** `.venv/bin/python -m compileall -q src/quantum_edge_core/contracts src/quantum_edge_core/external_intel src/quantum_edge_core/orchestrator tools/run_vst_session.py tools/qe_briefing.py tests/test_qe05_coingecko_external_intel.py` — успішно (код 0).
- **Регресійні тести (`pytest`):** `PYTHONPATH=src:. .venv/bin/pytest tests/test_phase*.py tests/test_qe*.py tests/test_zero*.py -v --hypothesis-seed=42`
  - **Результат:** `120 passed, 1 skipped` (включно з 5 новими тестами у [`tests/test_qe05_coingecko_external_intel.py`](file:///home/layman/QuantumEdge/tests/test_qe05_coingecko_external_intel.py), `test_phase4_prefix_cache.py` та `test_zero_duplication_guards.py`).
