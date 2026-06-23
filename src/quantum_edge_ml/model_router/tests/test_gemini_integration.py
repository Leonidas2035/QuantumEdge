import pytest
from unittest.mock import MagicMock, AsyncMock
from model_router.backends.google_gemini import GoogleGeminiBackend
from model_router.router.router import Router


# Mock response for google-genai SDK
class MockGenAIResponse:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_gemini_backend_success():
    # Setup mock
    backend = GoogleGeminiBackend()
    valid_text = '{"v":1,"s":"BUY","c":0.9,"r":"Moon","rk":"LOW"}'

    backend.client = MagicMock()
    backend.client.aio = MagicMock()
    backend.client.aio.models = MagicMock()
    backend.client.aio.models.generate_content = AsyncMock(
        return_value=MockGenAIResponse(valid_text)
    )

    # Execute
    result = await backend.generate("test prompt", system_prompt="sys", timeout_s=1.0)

    # Verify
    assert "BUY" in result
    assert "Moon" in result


@pytest.mark.asyncio
async def test_gemini_backend_failure():
    backend = GoogleGeminiBackend()
    backend.client = MagicMock()
    backend.client.aio = MagicMock()
    backend.client.aio.models = MagicMock()
    backend.client.aio.models.generate_content = AsyncMock(
        side_effect=Exception("API failure")
    )

    with pytest.raises(RuntimeError) as exc:
        await backend.generate("test", system_prompt="sys", timeout_s=1.0)
    assert "gemini_error" in str(exc.value)


@pytest.mark.asyncio
async def test_router_integration_with_gemini_mock(tmp_path):
    # Setup Router with Mocked Gemini Backend
    student = GoogleGeminiBackend()
    student.name = "gemini_student"
    student_text = '{"v":1,"s":"HOLD","c":0.5,"r":"Unsure","rk":"MED"}'

    student.client = MagicMock()
    student.client.aio = MagicMock()
    student.client.aio.models = MagicMock()
    student.client.aio.models.generate_content = AsyncMock(
        return_value=MockGenAIResponse(student_text)
    )

    # Use same backend for teacher for simplicity or mock another
    teacher = GoogleGeminiBackend()
    teacher.name = "gemini_teacher"
    teacher.client = MagicMock()
    teacher.client.aio = MagicMock()
    teacher.client.aio.models = MagicMock()
    teacher.client.aio.models.generate_content = AsyncMock(
        return_value=MockGenAIResponse(student_text)
    )

    router = Router(
        student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path
    )

    # Test Route
    result = await router.route("test prompt", hints={"mode": "local_first"})

    assert result.ok
    assert result.decision.s == "HOLD"
    assert result.backend == "student"
