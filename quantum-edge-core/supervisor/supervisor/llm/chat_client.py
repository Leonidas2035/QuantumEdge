"""Shared OpenAI Chat Completions client."""

from __future__ import annotations

import json
import logging
import os
from typing import List, Mapping
from urllib import error, request


class ChatCompletionsClient:
    """Thin wrapper around OpenAI chat completions API."""

    def __init__(self, api_url: str, api_key_env: str, logger: logging.Logger | None = None) -> None:
        self.api_url = api_url
        self.api_key_env = api_key_env
        self.logger = logger or logging.getLogger(__name__)

    def complete(self, model: str, messages: List[Mapping[str, str]], temperature: float, timeout_seconds: float) -> str:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"API key env var {self.api_key_env} not set")

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
        ).encode("utf-8")

        req = request.Request(
            self.api_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
                parsed = json.loads(body)
        except error.URLError as exc:  # pragma: no cover - network errors
            raise RuntimeError(f"Network error calling LLM: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from LLM: {exc}") from exc

        try:
            message = parsed["choices"][0]["message"]["content"]
            return message
        except Exception as exc:  # pragma: no cover - unexpected API change
            self.logger.error("Unexpected LLM response shape: %s", exc)
            raise RuntimeError("Unexpected LLM response shape") from exc
