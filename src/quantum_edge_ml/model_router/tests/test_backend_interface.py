import pytest
from model_router.decoding.enforce import enforce_decision


class BadBackend:
    name = "bad"

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        return "oops"


@pytest.mark.asyncio
async def test_backend_invalid_json_fallback():
    result = await enforce_decision("prompt", BadBackend(), timeout_s=1.0, max_attempts=2)
    assert result.ok is False
    assert result.decision.s == "HOLD"
    assert result.decision.r == "parse_fail"
