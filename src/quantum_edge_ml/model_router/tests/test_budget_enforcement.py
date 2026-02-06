from __future__ import annotations


from model_router.router.router import Router


class StaticBackend:
    name = "static"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        self.calls += 1
        return self.output


def test_budget_enforcement_skips_teacher(monkeypatch, tmp_path):
    monkeypatch.setenv("TEACHER_MAX_REQ_PER_DAY", "0")
    monkeypatch.setenv("TEACHER_MAX_TOKENS_PER_DAY", "0")

    student = StaticBackend("not json")
    teacher = StaticBackend('{"v":1,"s":"BUY","c":0.7,"sl":null,"tp":null,"r":"ok","rk":"LOW"}')
    router = Router(student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path)

    result = router.route("prompt", hints={"mode": "fallback"})
    assert result.backend in {"student", "fallback"}
    assert teacher.calls == 0
