"""Shared LLM Chat Completions client.

Supports both OpenAI-compatible and Google Gemini REST APIs.
The backend is auto-detected from the api_url:
  - URLs containing 'generativelanguage.googleapis.com' → Gemini
  - Everything else → OpenAI Chat Completions
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Mapping, Optional
from urllib import error, request


def _is_gemini_url(url: str) -> bool:
    return "generativelanguage.googleapis.com" in url


def _schema_to_dict(response_schema: Any) -> Dict[str, Any] | None:
    """Convert a Pydantic model class or dict to a JSON Schema dict.

    Accepts:
      - A Pydantic BaseModel **class** (not instance) → calls .model_json_schema()
      - A plain dict → returned as-is
      - None → returns None
    """
    if response_schema is None:
        return None
    # Pydantic v2 model class
    if hasattr(response_schema, "model_json_schema"):
        return response_schema.model_json_schema()
    # Pydantic v1 model class
    if hasattr(response_schema, "schema"):
        return response_schema.schema()
    if isinstance(response_schema, dict):
        return response_schema
    raise TypeError(
        f"response_schema must be a Pydantic model class or dict, got {type(response_schema)}"
    )


class ChatCompletionsClient:
    """Thin wrapper around LLM chat completions API (OpenAI + Gemini)."""

    def __init__(
        self, api_url: str, api_key_env: str, logger: logging.Logger | None = None
    ) -> None:
        self.api_url = api_url
        self.api_key_env = api_key_env
        self.logger = logger or logging.getLogger(__name__)

    # ── public API ───────────────────────────────────────────────────

    def complete(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
        response_schema: Any | None = None,
    ) -> str:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"API key env var {self.api_key_env} not set")

        schema_dict = _schema_to_dict(response_schema)

        if _is_gemini_url(self.api_url):
            return self._complete_gemini(
                model,
                messages,
                temperature,
                timeout_seconds,
                api_key,
                schema_dict=schema_dict,
            )
        return self._complete_openai(
            model,
            messages,
            temperature,
            timeout_seconds,
            api_key,
            schema_dict=schema_dict,
        )

    # ── Google Gemini ────────────────────────────────────────────────

    def _complete_gemini(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
        api_key: str,
        *,
        schema_dict: Dict[str, Any] | None = None,
    ) -> str:
        # Build Gemini REST URL.
        # If the config URL already contains the model and method, use as-is.
        # Otherwise, construct it from the base URL + model.
        url = self.api_url
        if ":generateContent" not in url:
            url = url.rstrip("/")
            url = f"{url}/{model}:generateContent"
        # Append API key as query parameter.
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={api_key}"

        # Convert OpenAI messages to Gemini "contents" format.
        # Gemini uses: system_instruction + contents[{role, parts}]
        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        gen_config: Dict[str, Any] = {
            "temperature": temperature,
        }

        # ── Structured Output (JSON Schema) ──────────────────
        if schema_dict is not None:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = schema_dict

        payload_dict: dict = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system_parts:
            payload_dict["systemInstruction"] = {
                "role": "user",
                "parts": system_parts,
            }

        payload = json.dumps(payload_dict).encode("utf-8")

        req = request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
                parsed = json.loads(body)
        except error.URLError as exc:
            raise RuntimeError(f"Network error calling LLM: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from LLM: {exc}") from exc

        # Parse Gemini response: candidates[0].content.parts[0].text
        try:
            candidate = parsed["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
            return text
        except (KeyError, IndexError, TypeError) as exc:
            self.logger.error(
                "Unexpected Gemini response: %s",
                json.dumps(parsed, indent=2, ensure_ascii=False)[:500],
            )
            raise RuntimeError("Unexpected Gemini response shape") from exc

    # ── OpenAI-compatible ────────────────────────────────────────────

    def _complete_openai(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
        api_key: str,
        *,
        schema_dict: Dict[str, Any] | None = None,
    ) -> str:
        payload_dict: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # ── Structured Output (JSON Schema) ──────────────────
        if schema_dict is not None:
            payload_dict["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "trading_decision",
                    "schema": schema_dict,
                    "strict": True,
                },
            }

        payload = json.dumps(payload_dict).encode("utf-8")

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
        except error.URLError as exc:
            raise RuntimeError(f"Network error calling LLM: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from LLM: {exc}") from exc

        try:
            message = parsed["choices"][0]["message"]["content"]
            return message
        except (KeyError, IndexError, TypeError) as exc:
            self.logger.error("Unexpected LLM response shape: %s", exc)
            raise RuntimeError("Unexpected LLM response shape") from exc
