from __future__ import annotations

import json

import httpx

from model_router.backends.openai_chat import OpenAIChatBackend
from model_router.backends.openai_responses import (
    OpenAIResponsesBackend,
    extract_text_from_responses,
)


def test_openai_responses_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("OPENAI_STORE", "false")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers.get("Authorization") == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-test"
        assert payload["store"] is False
        return httpx.Response(
            200,
            json={
                "output_text": '{"v":1,"s":"HOLD","c":0.1,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
            },
        )

    transport = httpx.MockTransport(handler)
    backend = OpenAIResponsesBackend(transport=transport)
    output = backend.generate("prompt", system_prompt="sys", timeout_s=2.0)
    assert output.startswith("{")


def test_openai_chat_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("Authorization") == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"v":1,"s":"HOLD","c":0.1,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    backend = OpenAIChatBackend(transport=transport)
    output = backend.generate("prompt", system_prompt="sys", timeout_s=2.0)
    assert output.startswith("{")


def test_responses_extract_output_text():
    payload = {"output_text": '{"v":1}'}
    assert extract_text_from_responses(payload) == '{"v":1}'


def test_responses_extract_output_array():
    payload = {
        "output": [
            {"type": "reasoning", "content": [{"type": "output_text", "text": ""}]},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"v":1}'}],
            },
        ]
    }
    assert extract_text_from_responses(payload) == '{"v":1}'


def test_responses_extract_multiple_segments():
    payload = {
        "output": [
            {"content": [{"type": "output_text", "text": '{"v":'}]},
            {"content": [{"type": "output_text", "text": "1}"}]},
        ]
    }
    assert extract_text_from_responses(payload) == '{"v":1}'
