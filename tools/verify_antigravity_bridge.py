#!/usr/bin/env python3
"""Verify OpenAI Function Calling compatibility of a local Antigravity proxy endpoint.

Checks:
  1. GET <base_url>/models — endpoint reachability and model availability.
  2. POST <base_url>/chat/completions — tool call emission with non-empty tool_call.id and valid JSON arguments.
  3. POST <base_url>/chat/completions — role="tool" round-trip preserving tool_call_id without 400/500 errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Sequence

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8045/v1"
DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_API_KEY = "sk-antigravity-local"

PING_TOOL_SPEC: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_ping_status",
            "description": "Return synthetic connectivity status for a target component.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Component identifier to ping, e.g. 'qe_bridge'",
                    }
                },
                "required": ["target"],
            },
        },
    }
]


def _normalize_base_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


def verify_bridge(
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    api_key: str = DEFAULT_API_KEY,
    *,
    timeout: float = 25.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Execute 2-step OpenAI tool-calling round-trip check and return structured JSON report."""
    norm_url = _normalize_base_url(base_url)
    t0 = time.perf_counter()

    report: dict[str, Any] = {
        "base_url": norm_url,
        "model": model,
        "models_ok": False,
        "available_models": [],
        "tool_call_ok": False,
        "tool_call_id": None,
        "tool_call_id_preserved": False,
        "roundtrip_ok": False,
        "final_reply": None,
        "latency_ms": 0.0,
        "error": None,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout, transport=transport, headers=headers) as client:
            # Step 1: GET /models
            models_resp = client.get(f"{norm_url}/models")
            if models_resp.status_code != 200:
                report["error"] = (
                    f"GET /models failed with HTTP {models_resp.status_code}: "
                    f"{models_resp.text[:200]}"
                )
                return report

            models_body = models_resp.json()
            raw_list = (
                models_body.get("data", [])
                if isinstance(models_body, dict)
                else (models_body if isinstance(models_body, list) else [])
            )
            model_ids = [
                str(item["id"])
                for item in raw_list
                if isinstance(item, dict) and item.get("id") is not None
            ]
            report["available_models"] = model_ids

            if model_ids and model not in model_ids:
                report["error"] = (
                    f"Requested model {model!r} not found in /models list: {model_ids}"
                )
                return report
            report["models_ok"] = True

            # Step 2: Trigger function call
            user_msg = {
                "role": "user",
                "content": (
                    "You MUST call the get_ping_status tool right now with "
                    'target="qe_bridge" before answering.'
                ),
            }
            step1_payload: dict[str, Any] = {
                "model": model,
                "messages": [user_msg],
                "tools": PING_TOOL_SPEC,
                "tool_choice": "auto",
                "temperature": 0.0,
            }
            step1_resp = client.post(
                f"{norm_url}/chat/completions", json=step1_payload
            )
            if step1_resp.status_code != 200:
                report["error"] = (
                    f"Step 1 POST /chat/completions failed with HTTP "
                    f"{step1_resp.status_code}: {step1_resp.text[:300]}"
                )
                return report

            step1_body = step1_resp.json()
            choices = step1_body.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                report["error"] = "Step 1 response missing choices[0]"
                return report

            assistant_msg = choices[0].get("message") or {}
            tool_calls = assistant_msg.get("tool_calls")
            if not isinstance(tool_calls, list) or len(tool_calls) == 0:
                report["error"] = (
                    "Step 1 response did not contain message.tool_calls; "
                    f"assistant content={assistant_msg.get('content')!r}"
                )
                return report

            tc0 = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
            tc_id = tc0.get("id")
            if not isinstance(tc_id, str) or not tc_id.strip():
                report["tool_call_id_preserved"] = False
                report["error"] = "Broken/empty tool_call.id in message.tool_calls[0]"
                return report

            fn_obj = tc0.get("function") if isinstance(tc0.get("function"), dict) else {}
            fn_name = fn_obj.get("name")
            raw_args = fn_obj.get("arguments")
            if fn_name != "get_ping_status":
                report["error"] = (
                    f"Unexpected function name in tool_call: {fn_name!r}"
                )
                return report

            try:
                if isinstance(raw_args, str):
                    parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                    args_json_str = raw_args
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                    args_json_str = json.dumps(raw_args)
                else:
                    raise ValueError(f"Unsupported arguments type: {type(raw_args).__name__}")
                if not isinstance(parsed_args, dict):
                    raise ValueError("Parsed function.arguments is not a JSON object")
            except Exception as exc:
                report["error"] = f"Invalid JSON in function.arguments: {exc}"
                return report

            report["tool_call_ok"] = True
            report["tool_call_id"] = tc_id
            report["tool_call_id_preserved"] = True

            # Step 3: Round-trip role="tool" message with preserved tool_call_id
            assistant_replay = {
                "role": "assistant",
                "content": assistant_msg.get("content"),
                "tool_calls": [
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": args_json_str,
                        },
                    }
                ],
            }
            tool_result_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "name": fn_name,
                "content": json.dumps(
                    {
                        "status": "pong",
                        "target": parsed_args.get("target", "qe_bridge"),
                        "ok": True,
                    }
                ),
            }
            step2_payload: dict[str, Any] = {
                "model": model,
                "messages": [user_msg, assistant_replay, tool_result_msg],
                "tools": PING_TOOL_SPEC,
                "temperature": 0.0,
            }
            step2_resp = client.post(
                f"{norm_url}/chat/completions", json=step2_payload
            )
            if step2_resp.status_code != 200:
                report["error"] = (
                    f"Step 2 tool round-trip failed with HTTP "
                    f"{step2_resp.status_code}: {step2_resp.text[:300]}"
                )
                return report

            step2_body = step2_resp.json()
            choices2 = step2_body.get("choices") or []
            if not choices2 or not isinstance(choices2[0], dict):
                report["error"] = "Step 2 response missing choices[0]"
                return report

            final_msg = choices2[0].get("message") or {}
            final_content = final_msg.get("content")
            if not isinstance(final_content, str) or not final_content.strip():
                report["error"] = (
                    f"Step 2 response missing non-empty text content: {final_msg!r}"
                )
                return report

            report["roundtrip_ok"] = True
            report["final_reply"] = final_content.strip()[:300]
            return report

    except httpx.RequestError as exc:
        report["error"] = f"Connection error: {exc}"
        return report
    except Exception as exc:
        report["error"] = f"Unexpected error: {exc}"
        return report
    finally:
        report["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_antigravity_bridge",
        description="Verify OpenAI /models and 2-step Function Calling round-trip on local Antigravity proxy",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AG_BASE_URL", DEFAULT_BASE_URL),
        help=f"OpenAI-compatible base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("AG_MODEL", DEFAULT_MODEL),
        help=f"Model ID to test (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AG_API_KEY", DEFAULT_API_KEY),
        help="Bearer API key for local proxy (default: sk-antigravity-local)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="HTTP request timeout in seconds (default: 25.0)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = verify_bridge(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout=args.timeout,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    all_ok = (
        result["models_ok"]
        and result["tool_call_ok"]
        and result["tool_call_id_preserved"]
        and result["roundtrip_ok"]
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
