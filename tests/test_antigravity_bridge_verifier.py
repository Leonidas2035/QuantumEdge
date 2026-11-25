"""Offline unit tests for tools/verify_antigravity_bridge.py and tools/run_hermes_via_antigravity.sh."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from typing import Any

import httpx
import yaml

import tools.verify_antigravity_bridge as bridge_verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SCRIPT = REPO_ROOT / "tools" / "run_hermes_via_antigravity.sh"


class TestVerifyAntigravityBridgeTransport:
    def test_successful_two_step_tool_calling_roundtrip(self):
        captured_requests: list[dict[str, Any]] = []
        chat_step = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal chat_step
            if request.method == "GET" and request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [
                            {"id": "gemini-4-hydrogen", "object": "model"},
                            {"id": "gemini-4-carbon", "object": "model"},
                        ],
                    },
                )

            if request.method == "POST" and request.url.path == "/v1/chat/completions":
                chat_step += 1
                body = json.loads(request.content.decode("utf-8"))
                captured_requests.append(body)

                if chat_step == 1:
                    return httpx.Response(
                        200,
                        json={
                            "id": "chatcmpl-step1",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "call_ag_98765",
                                                "type": "function",
                                                "function": {
                                                    "name": "get_ping_status",
                                                    "arguments": '{"target": "qe_bridge"}',
                                                },
                                            }
                                        ],
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                        },
                    )

                # Step 2: verify role="tool" and tool_call_id match
                messages = body["messages"]
                tool_msg = messages[-1]
                assert tool_msg["role"] == "tool"
                assert tool_msg["tool_call_id"] == "call_ag_98765"
                return httpx.Response(
                    200,
                    json={
                        "id": "chatcmpl-step2",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Ping status for qe_bridge is pong (ok=true).",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    },
                )

            return httpx.Response(404, json={"error": "not found"})

        report = bridge_verifier.verify_bridge(
            base_url="http://127.0.0.1:8045/v1",
            model="gemini-4-hydrogen",
            api_key="sk-antigravity-local",
            transport=httpx.MockTransport(handler),
        )

        assert report["models_ok"] is True
        assert report["tool_call_ok"] is True
        assert report["tool_call_id"] == "call_ag_98765"
        assert report["tool_call_id_preserved"] is True
        assert report["roundtrip_ok"] is True
        assert report["error"] is None
        assert len(captured_requests) == 2

    def test_detects_empty_or_broken_tool_call_id(self):
        def broken_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "gemini-4-hydrogen"}]},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "   ",
                                        "type": "function",
                                        "function": {
                                            "name": "get_ping_status",
                                            "arguments": '{"target": "qe_bridge"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )

        report = bridge_verifier.verify_bridge(
            base_url="http://127.0.0.1:8045/v1",
            model="gemini-4-hydrogen",
            transport=httpx.MockTransport(broken_handler),
        )

        assert report["models_ok"] is True
        assert report["tool_call_ok"] is False
        assert report["tool_call_id_preserved"] is False
        assert report["roundtrip_ok"] is False
        assert report["error"] is not None
        assert "tool_call.id" in report["error"]

    def test_handles_connection_refused_gracefully(self):
        # Bind an ephemeral socket and close it immediately to guarantee an unused local port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]

        report = bridge_verifier.verify_bridge(
            base_url=f"http://127.0.0.1:{closed_port}/v1",
            model="gemini-4-hydrogen",
            timeout=1.5,
        )

        assert report["models_ok"] is False
        assert report["tool_call_ok"] is False
        assert report["roundtrip_ok"] is False
        assert report["error"] is not None
        assert "Connection error" in report["error"]


class TestIsolatedHermesLauncherScript:
    def test_launcher_creates_isolated_config_in_runtime_and_blocks_global_hermes(self):
        # 1. Start an ephemeral loopback mock OpenAI server
        step_counter = {"n": 0}

        class _MockProxyHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path == "/v1/models":
                    payload = json.dumps({"data": [{"id": "gemini-4-hydrogen"}]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                step_counter["n"] += 1
                if step_counter["n"] == 1:
                    resp = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_local_srv_1",
                                            "type": "function",
                                            "function": {
                                                "name": "get_ping_status",
                                                "arguments": '{"target":"qe_bridge"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                else:
                    assert body["messages"][-1]["role"] == "tool"
                    assert body["messages"][-1]["tool_call_id"] == "call_local_srv_1"
                    resp = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "All good from mock local proxy.",
                                }
                            }
                        ]
                    }
                encoded = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _MockProxyHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        isolated_dir = REPO_ROOT / "runtime" / "test_hermes_ag_home_unit"
        try:
            proc = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER_SCRIPT),
                    "--check-only",
                    "--hermes-home",
                    str(isolated_dir),
                    "--base-url",
                    f"http://127.0.0.1:{port}/v1",
                    "--model",
                    "gemini-4-hydrogen",
                    "--api-key",
                    "sk-unit-test-local",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
            assert f"HERMES_HOME={isolated_dir}" in proc.stdout

            cfg_path = isolated_dir / "config.yaml"
            env_path = isolated_dir / ".env"
            assert cfg_path.is_file()
            assert env_path.is_file()

            cfg_data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            assert cfg_data["model"]["provider"] == "custom"
            assert cfg_data["model"]["base_url"] == f"http://127.0.0.1:{port}/v1"
            assert cfg_data["model"]["default"] == "gemini-4-hydrogen"
            assert "OPENAI_API_KEY=sk-unit-test-local" in env_path.read_text(encoding="utf-8")

            # Verify safety guard explicitly rejects ~/.hermes before writing anything
            fake_home = REPO_ROOT / "runtime" / "fake_user_home"
            blocked = subprocess.run(
                [
                    "bash",
                    str(LAUNCHER_SCRIPT),
                    "--check-only",
                    "--hermes-home",
                    str(fake_home / ".hermes"),
                ],
                env={**os.environ, "HOME": str(fake_home)},
                capture_output=True,
                text=True,
                check=False,
            )
            assert blocked.returncode == 2
            assert "Refusing to use global ~/.hermes" in blocked.stderr
            assert not (fake_home / ".hermes" / "config.yaml").exists()
        finally:
            server.shutdown()
            server.server_close()
            # Clean up temporary unit-test artifacts inside runtime/
            for p in (
                REPO_ROOT / "runtime" / "test_hermes_ag_home_unit",
                REPO_ROOT / "runtime" / "fake_user_home",
            ):
                if p.exists():
                    import shutil

                    shutil.rmtree(p, ignore_errors=True)
