# Supervisor LLM Stage 2/3 - Constrained JSON DecisionV1 + Router

Stage 2 adds a strict JSON control plane for supervisor decisions. It enforces a compact DecisionV1 schema, validates output, retries once on invalid output, and falls back to a safe HOLD decision when needed.

## Prerequisites

- Stage 1 engine is built and served (OpenAI-compatible) via `llm_engine/scripts/serve.sh`.
- Python deps: `httpx`, `fastapi`, `uvicorn` (plus optional `msgspec`, `orjson`).

## Run API

```bash
uvicorn supervisor_llm.api.app:app --host 127.0.0.1 --port 8010
```

## Call API

```bash
curl -s http://127.0.0.1:8010/v1/supervisor/decision \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Assess risk for 4% drawdown"}'
```

## Run CLI

```bash
python -m supervisor_llm.cli decision --prompt-file /path/to/prompt.txt
```

## Behavior

- Output is always a single-line compact JSON object with keys `v,s,c,sl,tp,r,rk`.
- Unknown keys or invalid values are rejected.
- On invalid output, one repair attempt runs; if still invalid, fallback decision is:
  `{\"v\":1,\"s\":\"HOLD\",\"c\":0.0,\"sl\":null,\"tp\":null,\"r\":\"parse_fail\",\"rk\":\"CRIT\"}`
- Audit log is written to `supervisor_llm/runtime/decisions.jsonl`.

## Stage 3 Router

Stage 3 adds a local-first router with optional OpenAI teacher, caching, budgets, circuit breakers, and distillation logs.

Quickstart (router endpoint):

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4o-mini
uvicorn supervisor_llm.api.app:app --host 127.0.0.1 --port 8010
curl -s http://127.0.0.1:8010/v1/supervisor/decision_routed \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Assess risk for 4% drawdown","mode":"local_first","teacher_ratio":0.1}'
```

Key env vars:

- `SUPERVISOR_ROUTER_MODE=local_first|shadow|fallback|ab`
- `SUPERVISOR_TEACHER_RATIO=0.1`
- `SUPERVISOR_CACHE_TTL_S=600`
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_USE_RESPONSES=1`, `OPENAI_STORE=false`
- `OPENAI_CONVERSATION_ENABLE=1` (optional, only when you want conversation state), `OPENAI_CONVERSATION_ID`
- `TEACHER_MAX_REQ_PER_DAY=200`, `TEACHER_MAX_TOKENS_PER_DAY=200000`
- `DISTILL_ENABLE=1`, `DISTILL_STORE_PROMPT=0`

Artifacts:

- Router events: `supervisor_llm/runtime/router_events.jsonl`
- Distill pairs: `supervisor_llm/runtime/distill/teacher_student_pairs.jsonl`
- Cache: `supervisor_llm/runtime/cache/router_cache.sqlite`
- Audit fallback: `supervisor_llm/runtime/audit_fallback.jsonl`

## Stage 4 QuestDB Context + Audit

Stage 4 adds optional QuestDB context retrieval for `/v1/supervisor/decision_routed` and decision audit logging via ILP.

Key env vars:

- `QDB_PG_DSN`, `QDB_TRADES_TABLE`, `QDB_TS_COL`, `QDB_SYMBOL_COL`, `QDB_PRICE_COL`, `QDB_AMOUNT_COL`
- `QDB_DECISIONS_TABLE=llm_decisions`, `QDB_ILP_HOST`, `QDB_ILP_PORT`
- `QDB_CONTEXT_CACHE_TTL_S=3`, `QDB_CONTEXT_MAX_CANDLES=5`

Use `with_context=true` plus `symbol` and `lookback_m` in the routed request body.

## Troubleshooting

- `parse_fail` in output: model returned invalid JSON or failed validation. Check the audit log.
- Backend unavailable: ensure `trtllm-serve` is running and `SUPERVISOR_LLM_BASE_URL` is correct.
- Invalid keys: see `docs/llm_contracts_decision_v1.md` for the exact schema.
