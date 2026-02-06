from __future__ import annotations

import os


class OpenAIChatBackend:
    def __init__(self, transport=None) -> None:
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        self.model = os.environ.get("OPENAI_MODEL", "")
        self.max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS", "128"))
        self.name = "openai_chat"
        self._transport = transport

    def _get_client(self, timeout_s: float):
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("httpx is required for OpenAI chat backend") from exc

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        if not self.model:
            raise RuntimeError("OPENAI_MODEL is required")

        headers = {"Authorization": f"Bearer {api_key}"}
        return httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout_s, transport=self._transport)

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
        }
        async with self._get_client(timeout_s) as client:
            resp = await client.post("/v1/chat/completions", json=payload)

        if resp.status_code == 429:
            raise RuntimeError("rate_limited")
        if resp.status_code >= 400:
            raise RuntimeError(f"openai_error:{resp.status_code}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("openai_empty_response")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise RuntimeError("openai_empty_response")
        return str(content).strip()
