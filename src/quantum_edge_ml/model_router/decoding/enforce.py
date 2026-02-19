from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from model_router.contracts.decision_v1 import (ValidationError,
                                                decode_decision,
                                                fallback_decision)
from model_router.decoding.repair_prompts import (SYSTEM_PROMPT,
                                                  make_repair_prompt,
                                                  make_user_prompt)


@dataclass
class EnforceResult:
    decision: object
    ok: bool
    attempts: int
    latency_ms: float
    error: Optional[str]
    backend: str
    raw_text: str


class BackendFailure(RuntimeError):
    pass


def _runtime_log_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    runtime_dir = root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / "decisions.jsonl"


def _append_audit(result: EnforceResult) -> None:
    decision_json = json.loads(result.decision.to_compact_json())
    payload = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "latency_ms": round(result.latency_ms, 2),
        "attempts": result.attempts,
        "backend": result.backend,
        "ok": result.ok,
        "decision": decision_json,
        "error": result.error,
    }
    path = _runtime_log_path()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
        )


async def enforce_decision(
    prompt: str, backend, *, timeout_s: float = 2.0, max_attempts: int = 2
) -> EnforceResult:
    user_prompt = make_user_prompt(prompt)
    attempts = 0
    last_error = None
    decision = None
    last_raw = ""
    ok = False
    start = time.perf_counter()

    current_prompt = user_prompt
    for attempt in range(max_attempts):
        attempts += 1
        try:
            raw = await backend.generate(
                current_prompt, system_prompt=SYSTEM_PROMPT, timeout_s=timeout_s
            )
            last_raw = raw
        except Exception as exc:
            last_error = f"backend_error:{exc}"
            break

        try:
            decision = decode_decision(raw)
            ok = True
            last_error = None
            break
        except ValidationError as exc:
            last_error = str(exc)
            if attempt + 1 < max_attempts:
                current_prompt = make_repair_prompt(user_prompt, raw, last_error)
            continue

    if not ok:
        decision = fallback_decision("parse_fail")

    latency_ms = (time.perf_counter() - start) * 1000.0
    result = EnforceResult(
        decision=decision,
        ok=ok,
        attempts=attempts,
        latency_ms=latency_ms,
        error=last_error,
        backend=getattr(backend, "name", backend.__class__.__name__),
        raw_text=last_raw,
    )
    _append_audit(result)
    return result
