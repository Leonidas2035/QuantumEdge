from __future__ import annotations

import os
from typing import Optional


def extract_text_from_responses(payload: dict) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"]).strip()

    parts = []
    for item in payload.get("output", []) or []:
        content = item.get("content", []) or []
        for part in content:
            if part.get("type") == "output_text" and "text" in part:
                parts.append(str(part.get("text", "")))

    text = "".join(parts).strip()
    if text:
        return text
    raise RuntimeError("openai_empty_response")


class OpenAIResponsesBackend:
    def __init__(self, transport=None) -> None:
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        self.model = os.environ.get("OPENAI_MODEL", "")
        self.store = os.environ.get("OPENAI_STORE", "false").lower() == "true"
        self.max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS", "128"))
        self.name = "openai_responses"
        self._transport = transport

    def _get_client(self, timeout_s: float):
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("httpx is required for OpenAI responses backend") from exc

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        if not self.model:
            raise RuntimeError("OPENAI_MODEL is required")

        headers = {"Authorization": f"Bearer {api_key}"}
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout_s, transport=self._transport)

    def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
            ],
            "temperature": 0.0,
            "max_output_tokens": self.max_tokens,
            "store": self.store,
        }
        if os.environ.get("OPENAI_CONVERSATION_ENABLE", "0") == "1":
            convo_id = os.environ.get("OPENAI_CONVERSATION_ID", "")
            if convo_id:
                payload["conversation"] = {"id": convo_id}
        with self._get_client(timeout_s) as client:
            resp = client.post("/v1/responses", json=payload)

        if resp.status_code == 429:
            raise RuntimeError("rate_limited")
        if resp.status_code >= 400:
            raise RuntimeError(f"openai_error:{resp.status_code}")

        data = resp.json()
        return extract_text_from_responses(data)
