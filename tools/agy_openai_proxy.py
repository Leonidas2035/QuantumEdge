#!/usr/bin/env python3
"""Local OpenAI-compatible HTTP proxy backed by Antigravity CLI (`agy`) Gemini 4 models.

Key design guarantees:
  - Listens strictly on 127.0.0.1 (default port 8045).
  - Delivers full, untruncated conversation + tool-result payloads (>100 KB) via stdin
    (`--input-format text --output-format json`), bypassing Linux argv limits.
  - Prevents double agent loops: runs `agy` in an empty isolated temp workspace with
    `--sandbox --disable-slash-commands` (without `--dangerously-skip-permissions`) and
    enforces `--json-schema` when OpenAI `tools` are supplied.
  - Supports both non-streaming JSON and SSE streaming (`stream=true`) for text and tool_calls.
  - Logs metadata only (no secrets/tokens) to `runtime/agy_openai_proxy.log`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Sequence
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGY_BIN = os.environ.get("AGY_BIN", "/home/layman/.local/bin/agy")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8045
DEFAULT_MODEL = "gemini-4-hydrogen-medium"
DEFAULT_TIMEOUT_SEC = 180
DEFAULT_KEEPALIVE_INTERVAL_SEC = 10.0
DEFAULT_LOG_PATH = REPO_ROOT / "runtime" / "agy_openai_proxy.log"

SUPPORTED_GEMINI4_MODELS: tuple[str, ...] = (
    "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-low",
    "gemini-4-carbon-medium",
    "gemini-4-carbon-high",
    "gemini-4-neon-medium",
    "gemini-4-neon-high",
    "gemini-4-argon-medium",
)

MODEL_ALIASES: dict[str, str] = {
    "gemini-4-hydrogen": "gemini-4-hydrogen-medium",
    "gemini-4-carbon": "gemini-4-carbon-medium",
    "gemini-4-neon": "gemini-4-neon-medium",
    "gemini-4-argon": "gemini-4-argon-medium",
}

TOOL_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {"type": ["string", "null"]},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments_json": {"type": "string"},
                },
                "required": ["name", "arguments_json"],
            },
        },
    },
    "required": ["content", "tool_calls"],
}


def resolve_agy_model(requested_model: str | None, default_model: str = DEFAULT_MODEL) -> str:
    if not requested_model:
        return default_model
    cleaned = requested_model.strip()
    return MODEL_ALIASES.get(cleaned, cleaned)


def _extract_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text") is not None:
                    parts.append(str(item["text"]))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def build_stateless_prompt(
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> tuple[str, bool]:
    """Serialize full OpenAI messages + tool definitions without any truncation.

    Returns (prompt_text, use_tool_schema).
    """
    tools_active = bool(tools) and tool_choice != "none"
    sections: list[str] = []

    if tools_active:
        forced_tool_name: str | None = None
        if isinstance(tool_choice, dict):
            fn = tool_choice.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                forced_tool_name = str(fn["name"])

        tool_specs_compact: list[dict[str, Any]] = []
        for t in tools or []:
            if isinstance(t, dict) and isinstance(t.get("function"), dict):
                fn = t["function"]
                tool_specs_compact.append(
                    {
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )

        sections.append(
            "=== STATELESS OPENAI FUNCTION-CALLING BACKEND ===\n"
            "CRITICAL PROTOCOL RULES:\n"
            "1. Do NOT invoke any internal Antigravity tools (NEVER call run_command, view_file, "
            "write_to_file, edit_file, search_web, or any built-in agent tool). You are acting "
            "purely as a stateless LLM inference engine.\n"
            "2. You MUST respond with a JSON object matching the enforced JSON schema: "
            '{"content": string|null, "tool_calls": [{"name": string, "arguments_json": string}]}.\n'
            "3. When you need the caller to execute one or more CALLER TOOLS listed below, populate "
            "`tool_calls` with exact tool `name` and compact valid JSON object string `arguments_json` "
            "(< 2000 characters), and set `content` to null.\n"
            "4. When all required `[TOOL_RESULT ...]` outputs are already present in the conversation "
            "history and no further tool call is needed, set `tool_calls` to `[]` and provide your "
            "final answer in `content` (<= 4500 characters).\n"
            + (
                f"5. MANDATORY TOOL CHOICE: You MUST call tool {forced_tool_name!r} in `tool_calls`.\n"
                if forced_tool_name
                else (
                    "5. MANDATORY TOOL CHOICE: `tool_calls` MUST contain at least one tool call.\n"
                    if tool_choice == "required"
                    else ""
                )
            )
            + "\nAVAILABLE CALLER TOOLS (JSON):\n"
            + json.dumps(tool_specs_compact, ensure_ascii=False)
        )
    else:
        sections.append(
            "=== STATELESS OPENAI CHAT BACKEND ===\n"
            "CRITICAL PROTOCOL RULES:\n"
            "1. Do NOT invoke any internal Antigravity tools (NEVER call run_command, view_file, "
            "write_to_file, or any built-in tool). Reply directly as a stateless assistant.\n"
            "2. Keep your response concise and <= 4500 characters unless explicitly asked otherwise."
        )

    sections.append("=== CONVERSATION TRANSCRIPT (UNTRUNCATED) ===")
    for msg in messages:
        role = str(msg.get("role") or "user").upper()
        text = _extract_content_text(msg.get("content"))

        if role == "ASSISTANT" and msg.get("tool_calls"):
            tc_lines: list[str] = []
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    tc_lines.append(
                        f"tool_call(id={tc.get('id')}, name={fn.get('name')}, arguments={fn.get('arguments')})"
                    )
            combined = (text + "\n" if text else "") + "\n".join(tc_lines)
            sections.append(f"[ASSISTANT]\n{combined}")
        elif role == "TOOL":
            tc_id = msg.get("tool_call_id") or ""
            t_name = msg.get("name") or ""
            sections.append(
                f"[TOOL_RESULT tool_call_id={tc_id} name={t_name}]\n{text}"
            )
        else:
            sections.append(f"[{role}]\n{text}")

    return "\n\n".join(sections), tools_active


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def invoke_agy_cli(
    prompt: str,
    *,
    agy_model: str,
    use_tool_schema: bool,
    agy_bin: str = DEFAULT_AGY_BIN,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    stateless_workdir: Path | None = None,
) -> dict[str, Any]:
    """Run `agy` headlessly via stdin with internal mutations disabled."""
    workdir = stateless_workdir or Path(tempfile.gettempdir()) / "qe_agy_stateless_ws"
    workdir.mkdir(parents=True, exist_ok=True)

    timeout_arg = f"{max(int(timeout_sec), 10)}s"
    cmd: list[str] = [
        agy_bin,
        "--model",
        agy_model,
        "--sandbox",
        "--disable-slash-commands",
        "--input-format",
        "text",
        "--output-format",
        "json",
        "--print-timeout",
        timeout_arg,
    ]
    if use_tool_schema:
        cmd.append(f"--json-schema={json.dumps(TOOL_RESPONSE_JSON_SCHEMA)}")

    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=str(workdir),
        timeout=timeout_sec + 10.0,
        check=False,
    )

    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(
            f"agy exited with code {proc.returncode}: {proc.stderr.strip()[:400]}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to decode agy JSON output: {exc}; stdout={proc.stdout[:300]!r}"
        ) from exc

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(
            f"agy returned non-SUCCESS status: {payload.get('error') or payload}"
        )

    return payload


def _normalize_arguments_json(raw_args: Any) -> str:
    if isinstance(raw_args, dict):
        return json.dumps(raw_args, ensure_ascii=False)
    if isinstance(raw_args, str):
        s = raw_args.strip()
        if not s:
            return "{}"
        parsed = json.loads(s)
        if not isinstance(parsed, dict):
            return json.dumps({"value": parsed}, ensure_ascii=False)
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(raw_args if raw_args is not None else {})


class AgyProxyEngine:
    """Core stateless translation layer from OpenAI Chat Completions to `agy` CLI."""

    def __init__(
        self,
        *,
        agy_bin: str = DEFAULT_AGY_BIN,
        default_model: str = DEFAULT_MODEL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        keepalive_interval_sec: float = DEFAULT_KEEPALIVE_INTERVAL_SEC,
        log_path: Path = DEFAULT_LOG_PATH,
        runner: Callable[..., dict[str, Any]] = invoke_agy_cli,
    ) -> None:
        self.agy_bin = agy_bin
        self.default_model = default_model
        self.timeout_sec = timeout_sec
        self.keepalive_interval_sec = keepalive_interval_sec
        self.log_path = log_path
        self.runner = runner

    def log_event(self, meta: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                **meta,
            }
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def list_models_payload(self) -> dict[str, Any]:
        now_ts = int(time.time())
        models = list(SUPPORTED_GEMINI4_MODELS)
        for alias in MODEL_ALIASES:
            if alias not in models:
                models.append(alias)
        return {
            "object": "list",
            "data": [
                {
                    "id": mid,
                    "object": "model",
                    "created": now_ts,
                    "owned_by": "google-antigravity",
                    "context_length": 262144,
                }
                for mid in models
            ],
        }

    def create_chat_completion(self, request_body: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        requested_model = str(request_body.get("model") or self.default_model)
        agy_model = resolve_agy_model(requested_model, default_model=self.default_model)
        messages = request_body.get("messages") or []
        tools = request_body.get("tools")
        tool_choice = request_body.get("tool_choice")

        prompt, use_tool_schema = build_stateless_prompt(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

        agy_out = self.runner(
            prompt,
            agy_model=agy_model,
            use_tool_schema=use_tool_schema,
            agy_bin=self.agy_bin,
            timeout_sec=self.timeout_sec,
        )

        raw_response_str = str(agy_out.get("response") or "")
        usage_raw = agy_out.get("usage") if isinstance(agy_out.get("usage"), dict) else {}
        prompt_tokens = int(usage_raw.get("input_tokens") or 0)
        completion_tokens = int(usage_raw.get("output_tokens") or 0)

        openai_tool_calls: list[dict[str, Any]] = []
        assistant_content: str | None = None
        finish_reason = "stop"

        if use_tool_schema:
            structured = agy_out.get("structured_output")
            if not isinstance(structured, dict):
                structured = _extract_json_object(raw_response_str)

            if isinstance(structured, dict):
                raw_tcs = structured.get("tool_calls") or []
                for tc in raw_tcs:
                    if isinstance(tc, dict) and tc.get("name"):
                        args_str = _normalize_arguments_json(
                            tc.get("arguments_json")
                            if "arguments_json" in tc
                            else tc.get("arguments")
                        )
                        openai_tool_calls.append(
                            {
                                "id": f"call_{uuid.uuid4().hex[:16]}",
                                "type": "function",
                                "function": {
                                    "name": str(tc["name"]),
                                    "arguments": args_str,
                                },
                            }
                        )
                raw_content = structured.get("content")
                if isinstance(raw_content, str) and raw_content.strip():
                    assistant_content = raw_content
            else:
                assistant_content = raw_response_str.strip()

            if openai_tool_calls:
                finish_reason = "tool_calls"
                if not assistant_content:
                    assistant_content = None
            else:
                finish_reason = "stop"
                if assistant_content is None:
                    assistant_content = raw_response_str.strip()
        else:
            assistant_content = raw_response_str.strip()
            finish_reason = "stop"

        msg_payload: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_content,
        }
        if openai_tool_calls:
            msg_payload["tool_calls"] = openai_tool_calls

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        output_chars = len(raw_response_str)
        self.log_event(
            {
                "model": requested_model,
                "agy_model": agy_model,
                "stream": bool(request_body.get("stream")),
                "input_chars": len(prompt),
                "output_chars": output_chars,
                "tool_calls_count": len(openai_tool_calls),
                "latency_ms": latency_ms,
                "status": "ok",
            }
        )

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:20]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model,
            "choices": [
                {
                    "index": 0,
                    "message": msg_payload,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }


def completion_to_sse_chunks(completion: dict[str, Any]) -> bytes:
    """Convert a standard OpenAI chat.completion object into SSE stream chunks."""
    cid = completion["id"]
    created = completion["created"]
    model = completion["model"]
    choice = completion["choices"][0]
    message = choice["message"]
    finish_reason = choice["finish_reason"]

    events: list[str] = []
    if message.get("tool_calls"):
        stream_tcs = [
            {
                "index": idx,
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for idx, tc in enumerate(message["tool_calls"])
        ]
        chunk1 = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": stream_tcs,
                    },
                    "finish_reason": None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk1, ensure_ascii=False)}\n\n")
    else:
        chunk1 = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": message.get("content") or "",
                    },
                    "finish_reason": None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk1, ensure_ascii=False)}\n\n")

    chunk_end = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ],
        "usage": completion.get("usage"),
    }
    events.append(f"data: {json.dumps(chunk_end, ensure_ascii=False)}\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events).encode("utf-8")


def make_http_handler(engine: AgyProxyEngine) -> type[BaseHTTPRequestHandler]:
    class _AgyOpenAIRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _safe_write(self, data: bytes, *, context: str = "response") -> bool:
            try:
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                engine.log_event(
                    {
                        "status": "client_disconnected",
                        "context": context,
                        "path": self.path.split("?", 1)[0],
                    }
                )
                return False

        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                engine.log_event(
                    {
                        "status": "client_disconnected",
                        "context": "headers",
                        "path": self.path.split("?", 1)[0],
                    }
                )
                return
            self._safe_write(encoded, context="json_body")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/v1/health"):
                self._send_json(
                    200,
                    {"status": "ok", "default_model": engine.default_model},
                )
                return
            if path in ("/v1/models", "/models"):
                self._send_json(200, engine.list_models_payload())
                return
            self._send_json(
                404,
                {"error": {"message": f"Unknown path: {path}", "type": "invalid_request_error"}},
            )

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path not in ("/v1/chat/completions", "/chat/completions"):
                self._send_json(
                    404,
                    {"error": {"message": f"Unknown path: {path}", "type": "invalid_request_error"}},
                )
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                request_body = json.loads(raw_body.decode("utf-8"))
                if not isinstance(request_body, dict):
                    raise ValueError("JSON body must be an object")
            except Exception as exc:
                self._send_json(
                    400,
                    {"error": {"message": f"Invalid request JSON: {exc}", "type": "invalid_request_error"}},
                )
                return

            try:
                if request_body.get("stream"):
                    self._handle_streaming_chat(request_body)
                else:
                    completion = engine.create_chat_completion(request_body)
                    self._send_json(200, completion)
            except (BrokenPipeError, ConnectionResetError):
                engine.log_event(
                    {
                        "status": "client_disconnected",
                        "context": "post_chat",
                        "path": path,
                    }
                )
            except Exception as exc:
                engine.log_event({"status": "error", "error": str(exc)[:300]})
                self._send_json(
                    500,
                    {"error": {"message": str(exc), "type": "proxy_runtime_error"}},
                )

        def _handle_streaming_chat(self, request_body: dict[str, Any]) -> None:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                engine.log_event({"status": "client_disconnected", "context": "sse_headers"})
                return

            done_event = threading.Event()
            result_box: dict[str, Any] = {}

            def _worker() -> None:
                try:
                    result_box["completion"] = engine.create_chat_completion(request_body)
                except Exception as exc:
                    result_box["error"] = exc
                finally:
                    done_event.set()

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            keepalive_interval = max(float(engine.keepalive_interval_sec), 0.05)
            while not done_event.wait(timeout=keepalive_interval):
                if not self._safe_write(b": keepalive\n\n", context="sse_keepalive"):
                    return

            if "error" in result_box:
                exc = result_box["error"]
                engine.log_event({"status": "error", "error": str(exc)[:300]})
                err_payload = {
                    "error": {"message": str(exc), "type": "proxy_runtime_error"}
                }
                self._safe_write(
                    f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode(
                        "utf-8"
                    ),
                    context="sse_error",
                )
                return

            sse_bytes = completion_to_sse_chunks(result_box["completion"])
            self._safe_write(sse_bytes, context="sse_completion")

    return _AgyOpenAIRequestHandler


def create_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    engine: AgyProxyEngine | None = None,
) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError(f"Security guard: proxy must bind only to 127.0.0.1/localhost (got {host!r})")
    active_engine = engine or AgyProxyEngine()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_http_handler(active_engine))
    server.daemon_threads = True
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy_openai_proxy",
        description="Local OpenAI-compatible HTTP proxy backed by Antigravity CLI (Gemini 4)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (strictly 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--default-model",
        default=DEFAULT_MODEL,
        help=f"Default Gemini 4 model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--agy-bin",
        default=DEFAULT_AGY_BIN,
        help=f"Path to agy binary (default: {DEFAULT_AGY_BIN})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"Per-turn agy timeout in seconds (default: {DEFAULT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"Metadata log file path (default: {DEFAULT_LOG_PATH})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = AgyProxyEngine(
        agy_bin=args.agy_bin,
        default_model=args.default_model,
        timeout_sec=args.timeout,
        log_path=args.log_path,
    )
    server = create_server(host=args.host, port=args.port, engine=engine)

    def _shutdown_handler(signum: int, frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    import threading

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    print(
        json.dumps(
            {
                "status": "listening",
                "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                "default_model": args.default_model,
                "log_path": str(args.log_path),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
