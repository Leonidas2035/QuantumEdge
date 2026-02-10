# Stage 4 - QuestDB Context Pack + Audit Log

Stage 4 adds optional QuestDB context retrieval for routed decisions and writes audit events to QuestDB via ILP (with a local fallback).

## Configuration

QuestDB read (PostgreSQL wire protocol):

- `QDB_PG_DSN="host=... port=8812 user=... password=... dbname=qdb"`
- `QDB_TRADES_TABLE=trades`
- `QDB_TS_COL=timestamp`
- `QDB_SYMBOL_COL=symbol`
- `QDB_PRICE_COL=price`
- `QDB_AMOUNT_COL=amount`

Context cache / size:

- `QDB_CONTEXT_CACHE_TTL_S=3`
- `QDB_CONTEXT_MAX_CANDLES=5`

Audit write (ILP):

- `QDB_DECISIONS_TABLE=llm_decisions`
- `QDB_ILP_HOST=127.0.0.1`
- `QDB_ILP_PORT=9009`

## Context format

ContextPackV1 (compact):

```
{v:1,sym:"BTCUSDT",lbm:15,t0:"...",t1:"...",ohlcv:[[t,o,h,l,c,v]],chg:0.01,vol:0.02}
```

Prompt prelude:

```
CTX|sym=BTCUSDT|lbm=15|chg=0.01|vol=0.02|cndl=[[t,o,h,l,c,v]]
```

## Example request

```bash
curl -s http://127.0.0.1:8010/v1/supervisor/decision_routed \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Assess risk","with_context":true,"symbol":"BTCUSDT","lookback_m":15,"mode":"local_first"}'
```

## Troubleshooting

- Missing QuestDB DSN: context is skipped and the request still succeeds.
- ILP unavailable: audit events are written to `supervisor_llm/runtime/audit_fallback.jsonl`.
- Table/column mismatch: update `QDB_*` env vars to match your schema.
