from __future__ import annotations

import json
import pytest

from model_router.router.router import Router


class StaticBackend:
    name = "static"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        self.calls += 1
        return self.outputs[min(self.calls - 1, len(self.outputs) - 1)]


@pytest.mark.asyncio
async def test_fallback_to_teacher(tmp_path):
    student = StaticBackend(["not json"])
    teacher = StaticBackend(['{"v":1,"s":"BUY","c":0.7,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'])
    router = Router(student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path)

    result = await router.route("prompt", hints={"mode": "fallback"})
    assert result.backend == "teacher"
    assert result.decision.s == "BUY"


@pytest.mark.asyncio
async def test_shadow_distill_logged(tmp_path):
    student = StaticBackend(['{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'])
    teacher = StaticBackend(['{"v":1,"s":"BUY","c":0.7,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'])
    router = Router(student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path)

    await router.route("prompt", hints={"mode": "shadow"})
    distill_path = tmp_path / "distill" / "teacher_student_pairs.jsonl"
    data = distill_path.read_text(encoding="utf-8").strip()
    record = json.loads(data)
    assert record["student"]["ok"] is True
    assert record["teacher"]["ok"] is True
