import pytest
from unittest.mock import MagicMock, AsyncMock
from model_router.backends.google_gemini import GoogleGeminiBackend
from model_router.router.router import Router


# Mock httpx response
class MockResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.text = "Mock Error" if status_code != 200 else ""

    def json(self):
        return self._json_data


# Mock httpx client context
class MockClientContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        mock_client = AsyncMock()
        mock_client.post.return_value = self.response
        return mock_client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_gemini_backend_success():
    # Setup mock
    backend = GoogleGeminiBackend()
    valid_response = {
        "candidates": [{"content": {"parts": [{"text": '{"v":1,"s":"BUY","c":0.9,"r":"Moon","rk":"LOW"}'}]}}]
    }

    backend._get_client = MagicMock(return_value=MockClientContext(MockResponse(200, valid_response)))

    # Execute
    result = await backend.generate("test prompt", system_prompt="sys", timeout_s=1.0)

    # Verify
    assert "BUY" in result
    assert "Moon" in result


@pytest.mark.asyncio
async def test_gemini_backend_failure():
    backend = GoogleGeminiBackend()
    backend._get_client = MagicMock(return_value=MockClientContext(MockResponse(500, {})))

    with pytest.raises(RuntimeError) as exc:
        await backend.generate("test", system_prompt="sys", timeout_s=1.0)
    assert "gemini_error:500" in str(exc.value)


@pytest.mark.asyncio
async def test_router_integration_with_gemini_mock(tmp_path):
    # Setup Router with Mocked Gemini Backend
    student = GoogleGeminiBackend()
    student.name = "gemini_student"

    valid_response = {
        "candidates": [{"content": {"parts": [{"text": '{"v":1,"s":"HOLD","c":0.5,"r":"Unsure","rk":"MED"}'}]}}]
    }
    student._get_client = MagicMock(return_value=MockClientContext(MockResponse(200, valid_response)))

    # Use same backend for teacher for simplicity or mock another
    teacher = GoogleGeminiBackend()
    teacher.name = "gemini_teacher"
    teacher._get_client = MagicMock(return_value=MockClientContext(MockResponse(200, valid_response)))

    router = Router(student_backend=student, teacher_backend=teacher, runtime_dir=tmp_path)

    # Test Route
    result = await router.route("test prompt", hints={"mode": "local_first"})

    assert result.ok
    assert result.decision.s == "HOLD"
    assert result.backend == "student"  # or gemini_student
