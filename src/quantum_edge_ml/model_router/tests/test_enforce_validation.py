from __future__ import annotations

import pytest
from model_router.decoding.enforce import enforce_decision


class StaticBackend:
    name = "static"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        self.calls += 1
        return self.output


@pytest.mark.asyncio
async def test_enforce_valid_output():
    backend = StaticBackend(
        '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    )
    result = await enforce_decision("test prompt", backend, timeout_s=1.0)
    assert result.ok is True
    assert result.attempts == 1
    assert result.decision.s == "HOLD"

