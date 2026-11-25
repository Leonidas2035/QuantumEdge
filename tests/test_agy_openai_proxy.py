"""Offline unit tests for tools/agy_openai_proxy.py (models, tool_calls, >100KB untruncated input, SSE stream)."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import httpx

import tools.agy_openai_proxy as agy_proxy


def _start_test_proxy(
    tmp_path: Path, runner: Any
) -> tuple[Any, str, Path]:
    log_path = tmp_path / "proxy_test.log"
    engine = agy_proxy.AgyProxyEngine(
        agy_bin="/home/layman/.local/bin/agy",
        default_model="gemini-4-hydrogen-medium",
        timeout_sec=30.0,
        log_path=log_path,
        runner=runner,
    )
    server = agy_proxy.create_server(host="127.0.0.1", port=0, engine=engine)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/v1", log_path


class TestAgyOpenAIProxyOffline:
    def test_get_models_returns_gemini4_list(self, tmp_path: Path):
        server, base_url, _ = _start_test_proxy(tmp_path, runner=lambda *a, **k: {})
        try:
            resp = httpx.get(f"{base_url}/models", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "list"
            ids = [item["id"] for item in data["data"]]
            assert "gemini-4-hydrogen-medium" in ids
            assert "gemini-4-carbon-medium" in ids
            assert "gemini-4-neon-medium" in ids
        finally:
            server.shutdown()
            server.server_close()

    def test_tool_call_and_role_tool_roundtrip(self, tmp_path: Path):
        captured_prompts: list[tuple[str, bool]] = []

        def fake_runner(prompt: str, *, agy_model: str, use_tool_schema: bool, **kwargs: Any) -> dict[str, Any]:
            captured_prompts.append((prompt, use_tool_schema))
            if len(captured_prompts) == 1:
                return {
                    "status": "SUCCESS",
                    "response": '{"content": null, "tool_calls": [{"name": "get_ping_status", "arguments_json": "{\\"target\\": \\"qe_bridge\\"}"}]}',
                    "structured_output": {
                        "content": None,
                        "tool_calls": [
                            {
                                "name": "get_ping_status",
                                "arguments_json": '{"target": "qe_bridge"}',
                            }
                        ],
                    },
                    "usage": {"input_tokens": 120, "output_tokens": 25},
                }
            return {
                "status": "SUCCESS",
                "response": '{"content": "Target qe_bridge responded with pong.", "tool_calls": []}',
                "structured_output": {
                    "content": "Target qe_bridge responded with pong.",
                    "tool_calls": [],
                },
                "usage": {"input_tokens": 180, "output_tokens": 18},
            }

        server, base_url, log_path = _start_test_proxy(tmp_path, runner=fake_runner)
        try:
            tools_spec = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_ping_status",
                        "description": "Check ping status",
                        "parameters": {
                            "type": "object",
                            "properties": {"target": {"type": "string"}},
                            "required": ["target"],
                        },
                    },
                }
            ]
            # Step 1: Model emits tool_calls
            r1 = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "gemini-4-hydrogen-medium",
                    "messages": [{"role": "user", "content": "Ping qe_bridge"}],
                    "tools": tools_spec,
                },
                timeout=5.0,
            )
            assert r1.status_code == 200
            b1 = r1.json()
            choice1 = b1["choices"][0]
            assert choice1["finish_reason"] == "tool_calls"
            tcs = choice1["message"]["tool_calls"]
            assert len(tcs) == 1
            tc_id = tcs[0]["id"]
            assert tc_id.startswith("call_")
            assert json.loads(tcs[0]["function"]["arguments"]) == {"target": "qe_bridge"}

            # Step 2: Round-trip with role="tool" + preserved tool_call_id
            r2 = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "gemini-4-hydrogen-medium",
                    "messages": [
                        {"role": "user", "content": "Ping qe_bridge"},
                        choice1["message"],
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": "get_ping_status",
                            "content": '{"status":"pong","target":"qe_bridge"}',
                        },
                    ],
                    "tools": tools_spec,
                },
                timeout=5.0,
            )
            assert r2.status_code == 200
            b2 = r2.json()
            choice2 = b2["choices"][0]
            assert choice2["finish_reason"] == "stop"
            assert "pong" in choice2["message"]["content"]

            # Verify Step 2 prompt passed exact tool_call_id and tool output into agy
            step2_prompt, step2_schema = captured_prompts[1]
            assert step2_schema is True
            assert tc_id in step2_prompt
            assert '{"status":"pong","target":"qe_bridge"}' in step2_prompt
            assert log_path.is_file()
        finally:
            server.shutdown()
            server.server_close()

    def test_large_over_100kb_tool_payload_passed_untruncated(self, tmp_path: Path):
        received_prompts: list[str] = []

        def fake_runner(prompt: str, **kwargs: Any) -> dict[str, Any]:
            received_prompts.append(prompt)
            return {
                "status": "SUCCESS",
                "response": "Full context analyzed without truncation.",
                "usage": {"input_tokens": 52000, "output_tokens": 12},
            }

        server, base_url, _ = _start_test_proxy(tmp_path, runner=fake_runner)
        try:
            large_market_blob = json.dumps(
                {
                    "marker_head": "HEAD_CTX_001",
                    "candles": [
                        {"i": i, "o": 65000.0 + i, "h": 65050.0 + i, "l": 64950.0 + i, "c": 65010.0 + i}
                        for i in range(2200)
                    ],
                    "marker_tail": "TAIL_CTX_999",
                }
            )
            assert len(large_market_blob.encode("utf-8")) > 110_000

            resp = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "gemini-4-carbon-medium",
                    "messages": [
                        {"role": "user", "content": "Inspect BTCUSDT context"},
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_ctx_full_1",
                                    "type": "function",
                                    "function": {"name": "get_context", "arguments": "{}"},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call_ctx_full_1",
                            "name": "get_context",
                            "content": large_market_blob,
                        },
                    ],
                },
                timeout=10.0,
            )
            assert resp.status_code == 200
            assert len(received_prompts) == 1
            assert "HEAD_CTX_001" in received_prompts[0]
            assert "TAIL_CTX_999" in received_prompts[0]
            assert len(received_prompts[0].encode("utf-8")) > 110_000
        finally:
            server.shutdown()
            server.server_close()

    def test_sse_streaming_chunks_for_text_and_tool_calls(self, tmp_path: Path):
        call_num = {"n": 0}

        def fake_runner(prompt: str, *, use_tool_schema: bool, **kwargs: Any) -> dict[str, Any]:
            call_num["n"] += 1
            if call_num["n"] == 1:
                return {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": {
                        "content": None,
                        "tool_calls": [
                            {"name": "terminal", "arguments_json": '{"command": "echo ok"}'}
                        ],
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 15},
                }
            return {
                "status": "SUCCESS",
                "response": "Streamed final answer.",
                "usage": {"input_tokens": 60, "output_tokens": 8},
            }

        server, base_url, _ = _start_test_proxy(tmp_path, runner=fake_runner)
        try:
            # 1. Streaming tool_calls
            r_tc = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "gemini-4-hydrogen-medium",
                    "stream": True,
                    "messages": [{"role": "user", "content": "run echo"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "terminal", "parameters": {"type": "object"}},
                        }
                    ],
                },
                timeout=5.0,
            )
            assert r_tc.status_code == 200
            assert "text/event-stream" in r_tc.headers["content-type"]
            lines_tc = [ln for ln in r_tc.text.splitlines() if ln.startswith("data: ")]
            assert lines_tc[-1] == "data: [DONE]"
            first_chunk = json.loads(lines_tc[0][6:])
            assert first_chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "terminal"
            end_chunk = json.loads(lines_tc[1][6:])
            assert end_chunk["choices"][0]["finish_reason"] == "tool_calls"

            # 2. Streaming plain text
            r_txt = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "gemini-4-hydrogen-medium",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                },
                timeout=5.0,
            )
            assert r_txt.status_code == 200
            lines_txt = [ln for ln in r_txt.text.splitlines() if ln.startswith("data: ")]
            assert lines_txt[-1] == "data: [DONE]"
            txt_chunk = json.loads(lines_txt[0][6:])
            assert txt_chunk["choices"][0]["delta"]["content"] == "Streamed final answer."
        finally:
            server.shutdown()
            server.server_close()
