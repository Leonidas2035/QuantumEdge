from __future__ import annotations

import os

import time

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel, Field

from model_router.audit.events import AuditEvent
from model_router.audit.questdb_ilp import QuestDBAuditWriter
from model_router.backends.openai_chat import OpenAIChatBackend
from model_router.backends.openai_responses import OpenAIResponsesBackend
from model_router.backends.trtllm_openai_compat import OpenAICompatBackend
from model_router.contracts.decision_v1 import fallback_decision
from model_router.decoding.enforce import enforce_decision
from model_router.context.fetcher import build_fetcher_from_env
from model_router.context.formatter import ContextFormatter
from model_router.router.redaction import hash_prompt
from model_router.router.router import Router

from model_router.backends.google_gemini import GoogleGeminiBackend

app = FastAPI(title="supervisor-llm", version="0.1.0")


class DecisionRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    timeout_s: float = Field(2.0, ge=0.1, le=30.0)


class RoutedDecisionRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    timeout_s: float = Field(2.0, ge=0.1, le=30.0)
    mode: str = Field("local_first")
    teacher_ratio: float = Field(0.1, ge=0.0, le=1.0)
    force_teacher: bool = Field(False)
    symbol: str | None = None
    lookback_m: int = Field(15, ge=1, le=240)
    with_context: bool = False


_backend = None
_router = None
_context_fetcher = None
_audit_writer = None


def _get_backend():
    global _backend
    if _backend is None:
        backend_type = os.environ.get("SUPERVISOR_LLM_BACKEND", "google_gemini")
        if backend_type == "google_gemini":
            _backend = GoogleGeminiBackend()
        elif backend_type == "openai_compat":
            _backend = OpenAICompatBackend()
        else:
            raise RuntimeError(f"Unsupported backend: {backend_type}")
    return _backend


def _get_router() -> Router:
    global _router
    if _router is None:
        # Default to Gemini for student/primary
        student = GoogleGeminiBackend()
        use_responses = os.environ.get("OPENAI_USE_RESPONSES", "1") == "1"
        teacher = OpenAIResponsesBackend() if use_responses else OpenAIChatBackend()
        _router = Router(student_backend=student, teacher_backend=teacher)
    return _router


def _get_context_fetcher():
    global _context_fetcher
    if _context_fetcher is None:
        _context_fetcher = build_fetcher_from_env()
    return _context_fetcher


def _get_audit_writer() -> QuestDBAuditWriter:
    global _audit_writer
    if _audit_writer is None:
        _audit_writer = QuestDBAuditWriter()
    return _audit_writer


@app.post("/v1/supervisor/decision")
async def decision(req: DecisionRequest) -> Response:
    try:
        backend = _get_backend()
        result = await enforce_decision(req.prompt, backend, timeout_s=req.timeout_s)
        payload = result.decision.to_compact_json()
    except Exception:
        payload = fallback_decision("parse_fail").to_compact_json()
    return Response(content=payload, media_type="application/json")


@app.post("/v1/supervisor/decision_routed")
async def decision_routed(req: RoutedDecisionRequest) -> Response:
    try:
        router = _get_router()
        prompt = req.prompt
        if req.with_context and req.symbol:
            try:
                fetcher = _get_context_fetcher()
                pack = fetcher.get_context(req.symbol, req.lookback_m)
                formatter = ContextFormatter()
                ctx_line = formatter.format(pack)
                prompt = f"{ctx_line}\n{req.prompt}"
            except Exception:
                prompt = req.prompt
        hints = {"mode": req.mode, "teacher_ratio": req.teacher_ratio, "force_teacher": req.force_teacher}
        result = await router.route(prompt, timeout_s=req.timeout_s, hints=hints)
        payload = result.decision.to_compact_json()
        try:
            audit = _get_audit_writer()
            model_id = os.environ.get("SUPERVISOR_LLM_MODEL", "local")
            if result.backend == "teacher":
                model_id = os.environ.get("OPENAI_MODEL", "openai")
            event = AuditEvent(
                ts_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                sym=req.symbol,
                backend_used=result.backend,
                model_id=model_id,
                mode=req.mode,
                decision_json=payload,
                confidence=result.decision.c,
                risk=result.decision.rk,
                latency_ms=result.latency_ms,
                prompt_hash=hash_prompt(req.prompt),
                ok=result.ok,
                error_code=result.error,
            )
            audit.write(event)
        except Exception:
            pass
    except Exception:
        payload = fallback_decision("parse_fail").to_compact_json()
    return Response(content=payload, media_type="application/json")


@app.get("/v1/supervisor/health")
def health() -> dict:
    router = _get_router()
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    qdb_ok = False
    try:
        _get_context_fetcher()
        qdb_ok = True
    except Exception:
        qdb_ok = False
    return {
        "timestamp_utc": now_utc,
        "budgets": router.budgets.remaining(now_utc),
        "circuits": {
            "student": router.student_circuit.snapshot(),
            "teacher": router.teacher_circuit.snapshot(),
        },
        "questdb": {"available": qdb_ok},
    }
