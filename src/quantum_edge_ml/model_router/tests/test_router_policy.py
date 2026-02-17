from __future__ import annotations

from model_router.router.policy import RouterPolicy
from model_router.router.router import Router


class StaticBackend:
    name = "static"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        self.calls += 1
        return self.output


def test_ab_deterministic_choice(tmp_path):
    policy = RouterPolicy(mode="ab", teacher_ratio=0.5, force_teacher=False)
    student = StaticBackend(
        '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    )
    teacher = StaticBackend(
        '{"v":1,"s":"BUY","c":0.7,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    )
    router = Router(
        student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path
    )

    prompt = "risk check"
    hints = {"mode": policy.mode, "teacher_ratio": policy.teacher_ratio}
    result1 = router.route(prompt, hints=hints)
    result2 = router.route(prompt, hints=hints)

    assert result1.backend == result2.backend


def test_local_first_student_ok(tmp_path):
    student = StaticBackend(
        '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    )
    teacher = StaticBackend(
        '{"v":1,"s":"BUY","c":0.7,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    )
    router = Router(
        student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path
    )

    result = router.route("prompt", hints={"mode": "local_first"})
    assert result.backend == "student"
    assert teacher.calls == 0
