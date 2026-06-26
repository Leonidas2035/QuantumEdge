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


def _clean_schema(schema: Any) -> Any:
    """Recursively remove regex 'pattern' keys from JSON schema to prevent look-around errors in grammar parsers."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k != "pattern"}
    elif isinstance(schema, list):
        return [_clean_schema(item) for item in schema]
    return schema


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
        raw_schema = response_schema.model_json_schema()
    # Pydantic v1 model class
    elif hasattr(response_schema, "schema"):
        raw_schema = response_schema.schema()
    elif isinstance(response_schema, dict):
        raw_schema = response_schema
    else:
        raise TypeError(
            f"response_schema must be a Pydantic model class or dict, got {type(response_schema)}"
        )
    return _clean_schema(raw_schema)


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
        self.logger.debug(f"[CHAT_CLIENT] DEBUG: complete() called with model={model}, api_url={self.api_url}")
        schema_dict = _schema_to_dict(response_schema)

        if self.api_url == "hermes":
            res = self._complete_hermes(
                model,
                messages,
                temperature,
                timeout_seconds,
                schema_dict=schema_dict,
            )
        else:
            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise RuntimeError(f"API key env var {self.api_key_env} not set")

            if _is_gemini_url(self.api_url):
                res = self._complete_gemini(
                    model,
                    messages,
                    temperature,
                    timeout_seconds,
                    api_key,
                    schema_dict=schema_dict,
                )
            else:
                res = self._complete_openai(
                    model,
                    messages,
                    temperature,
                    timeout_seconds,
                    api_key,
                    schema_dict=schema_dict,
                )

        try:
            from hermes.supervisor.utils.dataset_collector import (
                collect_llm_sample,
            )

            collect_llm_sample(messages, res, model)
        except Exception as collector_err:
            self.logger.warning(
                "Failed to collect LLM sample for dataset: %s",
                collector_err,
            )

        return res

    # ── Hermes CLI ───────────────────────────────────────────────────

    def _complete_hermes(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
        *,
        schema_dict: Dict[str, Any] | None = None,
    ) -> str:
        # First, try to call Gemini API natively using ADC or API key
        try:
            # 1. Resolve API key
            api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
                "GEMINI_API_KEY"
            )

            # Convert messages to Gemini format
            system_parts = []
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                text = msg.get("content", "")
                if role == "system":
                    system_parts.append({"text": text})
                else:
                    gemini_role = "model" if role == "assistant" else "user"
                    contents.append({"role": gemini_role, "parts": [{"text": text}]})

            gen_config = {"temperature": temperature}
            if schema_dict is not None:
                gen_config["responseMimeType"] = "application/json"
                gen_config["responseSchema"] = schema_dict

            payload_dict = {
                "contents": contents,
                "generationConfig": gen_config,
            }
            if system_parts:
                payload_dict["systemInstruction"] = {
                    "role": "user",
                    "parts": system_parts,
                }
            payload = json.dumps(payload_dict).encode("utf-8")

            headers = {"Content-Type": "application/json"}

            if api_key:
                # Use Google AI Studio REST API
                gemini_model = (
                    "gemini-1.5-pro" if not model or model == "hermes" else model
                )
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}"
                self.logger.info(
                    "Calling Gemini API natively via AI Studio with model: %s",
                    gemini_model,
                )
            else:
                # Try Google Application Default Credentials (ADC) via Vertex AI
                import google.auth
                import google.auth.transport.requests

                credentials, project_id = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                req_auth = google.auth.transport.requests.Request()
                credentials.refresh(req_auth)
                token = credentials.token

                if not token or not project_id:
                    raise RuntimeError("No ADC token or project ID found")

                headers["Authorization"] = f"Bearer {token}"
                headers["x-goog-user-project"] = project_id

                region = (
                    os.environ.get("VERTEX_LOCATION")
                    or os.environ.get("GOOGLE_CLOUD_REGION")
                    or "us-central1"
                )
                gemini_model = (
                    "gemini-2.5-flash" if not model or model == "hermes" else model
                )
                if gemini_model == "gemini-1.5-pro":
                    gemini_model = "gemini-2.5-pro"
                elif gemini_model == "gemini-1.5-flash":
                    gemini_model = "gemini-2.5-flash"

                url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{region}/publishers/google/models/{gemini_model}:generateContent"
                self.logger.info(
                    "Calling Gemini API natively via Vertex AI (ADC) with model: %s, region: %s",
                    gemini_model,
                    region,
                )

            req = request.Request(url, data=payload, headers=headers, method="POST")
            with request.urlopen(req, timeout=timeout_seconds or 30.0) as resp:
                body = resp.read().decode("utf-8")
                parsed = json.loads(body)

            candidate = parsed["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
            self.logger.info("Native Gemini API call succeeded.")
            return text.strip()

        except Exception as native_exc:
            self.logger.warning(
                "Native Gemini API call failed: %s. Falling back to Hermes CLI.",
                native_exc,
            )

        # Build prompt from messages for CLI
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            prompt_parts.append(f"### {role}:\n{content}")

        prompt_str = "\n\n".join(prompt_parts)

        if schema_dict is not None:
            prompt_str += (
                f"\n\nCRITICAL: You must respond ONLY with a valid JSON object matching this schema:\n"
                f"{json.dumps(schema_dict, indent=2)}\n"
                f"Do not include any other text, markdown fences, or comments. Raw JSON only."
            )

        import subprocess

        # Call the user's local hermes command
        cmd = ["/home/korben/.local/bin/hermes", "-z", prompt_str]
        if model and model != "hermes":
            cmd.extend(["--model", model])

        try:
            # Run with timeout to prevent hangs
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_seconds or 60.0
            )
            if res.returncode != 0:
                self.logger.error("Hermes CLI error: %s", res.stderr)
                raise RuntimeError(f"Hermes CLI failed: {res.stderr}")
            return res.stdout.strip()
        except Exception as exc:
            self.logger.error("Hermes CLI execution failed or timed out: %s", exc)
            if schema_dict:
                fallback_data = {
                    "market_regime": "ranging",
                    "grid_bias": "neutral",
                    "recommended_grid_top": 75000.0,
                    "recommended_grid_bottom": 65000.0,
                    "capital_exposure_pct": 10.0,
                    "grid_spacing_multiplier": 1.0,
                }
                self.logger.warning("Using schema-compliant fallback JSON for Hermes.")
                return json.dumps(fallback_data)
            raise exc

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

        self.logger.debug(f"[CHAT_CLIENT] DEBUG: Sending OpenAI request to {self.api_url} with model {model}, timeout {timeout_seconds}s...")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                self.logger.debug(f"[CHAT_CLIENT] DEBUG: Response status {resp.status}")
                body = resp.read().decode("utf-8")
                self.logger.debug(f"[CHAT_CLIENT] DEBUG: Response read complete.")
                parsed = json.loads(body)
        except error.HTTPError as exc:
            body_err = ""
            try:
                body_err = exc.read().decode("utf-8")
            except Exception:
                pass
            err_msg = f"{exc.reason} {body_err}"
            if exc.code in (400, 404) and (
                "support tool use" in err_msg.lower()
                or "tools" in err_msg.lower()
                or "response_format" in err_msg.lower()
            ):
                self.logger.warning(
                    "LLM endpoint does not support tools/response_format. Executing fallback without them."
                )
                payload_dict_fallback = {
                    k: v
                    for k, v in payload_dict.items()
                    if k not in ("tools", "response_format")
                }
                payload_fallback = json.dumps(payload_dict_fallback).encode("utf-8")
                req_fallback = request.Request(
                    self.api_url,
                    data=payload_fallback,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    method="POST",
                )
                try:
                    with request.urlopen(req_fallback, timeout=timeout_seconds) as resp:
                        body = resp.read().decode("utf-8")
                        parsed = json.loads(body)
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"Fallback call failed: {fallback_exc}"
                    ) from fallback_exc
            else:
                raise RuntimeError(
                    f"HTTP error calling LLM: {exc.code} - {err_msg}"
                ) from exc
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
