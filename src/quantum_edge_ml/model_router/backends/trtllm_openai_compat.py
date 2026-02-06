from __future__ import annotations

import os
from typing import Optional


class OpenAICompatBackend:
    def __init__(self) -> None:
        self.base_url = os.environ.get("SUPERVISOR_LLM_BASE_URL", "http://127.0.0.1:8000")
        self.model = os.environ.get("SUPERVISOR_LLM_MODEL", "gemma3-4b")
        self.max_tokens = int(os.environ.get("SUPERVISOR_LLM_MAX_TOKENS", "128"))
        self.name = "trtllm_openai_compat"
        self._mode: Optional[str] = None
        self._models_checked = False

    def _get_client(self, timeout_s: Optional[float] = None):
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("httpx is required for OpenAI-compatible backend") from exc
        return httpx.AsyncClient(timeout=timeout_s, base_url=self.base_url)

    async def _check_models(self) -> None:
        if self._models_checked:
            return
        async with self._get_client(timeout_s=5.0) as client:
            resp = await client.get("/v1/models")
        if resp.status_code != 200:
            raise RuntimeError(
                f"/v1/models unavailable at {self.base_url} (status={resp.status_code})"
            )
        self._models_checked = True

    async def _post_chat(self, client, prompt: str, system_prompt: str):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": self.max_tokens,
        }
        return await client.post("/v1/chat/completions", json=payload)

    async def _post_completions(self, client, prompt: str, system_prompt: str):
        combined = f"{system_prompt}\nUser: {prompt}\nAssistant:"
        payload = {
            "model": self.model,
            "prompt": combined,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": self.max_tokens,
        }
        return await client.post("/v1/completions", json=payload)

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        await self._check_models()
        async with self._get_client(timeout_s=timeout_s) as client:
            if self._mode is None:
                resp = await self._post_chat(client, prompt, system_prompt)
                if resp.status_code == 404:
                    resp = await self._post_completions(client, prompt, system_prompt)
                    if resp.status_code == 404:
                        raise RuntimeError("Neither chat nor completions endpoint available")
                    self._mode = "completions"
                else:
                    self._mode = "chat"
            else:
                if self._mode == "chat":
                    resp = await self._post_chat(client, prompt, system_prompt)
                else:
                    resp = await self._post_completions(client, prompt, system_prompt)

        if resp.status_code != 200:
            raise RuntimeError(f"LLM request failed: {resp.status_code} {resp.text}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response missing choices")

        if self._mode == "chat":
            message = choices[0].get("message") or {}
            content = message.get("content")
        else:
            content = choices[0].get("text")

        if content is None:
            raise RuntimeError("LLM response missing content")
        return str(content).strip()
