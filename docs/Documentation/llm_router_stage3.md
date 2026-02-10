# Stage 3 Router - Local-first + OpenAI Teacher

Stage 3 adds a deterministic router that prefers the local Gemma 3 4B engine and optionally calls OpenAI as a teacher for shadowing/fallback/AB sampling. All outputs are validated against DecisionV1 and returned as compact JSON.

## Modes

- `local_first`: local student is primary; teacher used only if local fails.
- `shadow`: local student returns; teacher is called for distillation (if budgets allow).
- `fallback`: local first, teacher only on local failure.
- `ab`: deterministic hash routes a percentage to teacher and returns teacher result.

## Configuration

Router:
- `SUPERVISOR_ROUTER_MODE=local_first|shadow|fallback|ab`
- `SUPERVISOR_TEACHER_RATIO=0.1`
- `SUPERVISOR_CACHE_TTL_S=600`

Teacher budgets:
- `TEACHER_MAX_REQ_PER_DAY=200`
- `TEACHER_MAX_TOKENS_PER_DAY=200000`

OpenAI:
- `OPENAI_API_KEY` (required for teacher)
- `OPENAI_BASE_URL=https://api.openai.com`
- `OPENAI_MODEL=...`
- `OPENAI_USE_RESPONSES=1`
- `OPENAI_STORE=false` (data control; when supported, disables storage)
- `OPENAI_CONVERSATION_ENABLE=1` and `OPENAI_CONVERSATION_ID` (only if you want conversation state)

Distillation:
- `DISTILL_ENABLE=1`
- `DISTILL_STORE_PROMPT=0`

## Distill format

File: `supervisor_llm/runtime/distill/teacher_student_pairs.jsonl`

Each line:

```json
{
  "ts_utc": "...",
  "schema": "DecisionV1",
  "prompt_hash": "...",
  "prompt_redacted": "...",
  "student": {"ok": true, "decision": {"v":1,...}, "raw_hash": "..."},
  "teacher": {"ok": true, "decision": {"v":1,...}, "raw_hash": "..."},
  "diff": {"same_action": true, "confidence_delta": 0.12, "risk_delta": "LOW->MED", "notes": "shadow"},
  "backend_meta": {"student_model": "...", "teacher_model": "...", "lat_ms_student": 12.3, "lat_ms_teacher": 88.1}
}
```

## API

- `POST /v1/supervisor/decision_routed`
- `GET /v1/supervisor/health`

## Determinism

AB routing uses a stable hash of `prompt_hash + day_bucket` to avoid flapping. Cache keys include schema, model id, prompt hash, and mode for repeatable results.
