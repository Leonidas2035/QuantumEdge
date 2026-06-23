from __future__ import annotations

import pytest
from model_router.decoding.enforce import enforce_decision


class SequenceBackend:
    name = "sequence"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        self.calls += 1
        return self.outputs[min(self.calls - 1, len(self.outputs) - 1)]


@pytest.mark.asyncio
async def test_repair_loop_succeeds():
    outputs = [
        "not json",
        '{"v":1,"s":"HOLD","c":0.1,"sl":null,"tp":null,"r":"ok","rk":"LOW"}',
    ]
    backend = SequenceBackend(outputs)
    result = await enforce_decision("prompt", backend, timeout_s=1.0, max_attempts=2)
    assert result.ok is True
    assert result.attempts == 2

