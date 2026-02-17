from __future__ import annotations

import argparse
import os
import sys

from model_router.backends.openai_chat import OpenAIChatBackend
from model_router.backends.openai_responses import OpenAIResponsesBackend
from model_router.backends.trtllm_openai_compat import OpenAICompatBackend
from model_router.contracts.decision_v1 import fallback_decision
from model_router.decoding.enforce import enforce_decision
from model_router.router.router import Router


def _read_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _build_router() -> Router:
    student = OpenAICompatBackend()
    use_responses = os.environ.get("OPENAI_USE_RESPONSES", "1") == "1"
    teacher = OpenAIResponsesBackend() if use_responses else OpenAIChatBackend()
    return Router(student_backend=student, teacher_backend=teacher)


def _route_decision(
    prompt: str, timeout_s: float, mode: str, teacher_ratio: float, force_teacher: bool
):
    router = _build_router()
    hints = {
        "mode": mode,
        "teacher_ratio": teacher_ratio,
        "force_teacher": force_teacher,
    }
    return router.route(prompt, timeout_s=timeout_s, hints=hints)


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervisor LLM decision CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    decision = sub.add_parser("decision", help="Generate DecisionV1 JSON")
    decision.add_argument("--prompt-file", required=True)
    decision.add_argument("--timeout-s", type=float, default=2.0)
    decision.add_argument("--routed", action="store_true", help="Use Stage 3 router")
    decision.add_argument("--mode", default="local_first", help="Router mode")
    decision.add_argument("--teacher-ratio", type=float, default=0.1)
    decision.add_argument("--force-teacher", action="store_true")
    args = parser.parse_args()

    prompt = _read_prompt(args.prompt_file)
    routed_default = os.environ.get("SUPERVISOR_CLI_ROUTED_DEFAULT", "0") == "1"
    use_routed = args.routed or routed_default
    exit_code = 0
    try:
        if use_routed:
            result = _route_decision(
                prompt,
                timeout_s=args.timeout_s,
                mode=args.mode,
                teacher_ratio=args.teacher_ratio,
                force_teacher=args.force_teacher,
            )
            payload = result.decision.to_compact_json()
            if result.error:
                exit_code = 2
        else:
            backend = OpenAICompatBackend()
            result = enforce_decision(prompt, backend, timeout_s=args.timeout_s)
            payload = result.decision.to_compact_json()
            if result.error and str(result.error).startswith("backend_error"):
                exit_code = 2
    except Exception:
        payload = fallback_decision("parse_fail").to_compact_json()
        exit_code = 2

    sys.stdout.write(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
