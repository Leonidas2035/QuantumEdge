"""Lightweight HTTP API exposing heartbeat and risk evaluation."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor import SupervisorApp  # type: ignore


@dataclass
class ApiServerConfig:
    host: str
    port: int
    auth_token: str


class ApiServer:
    """Simple JSON API server running in a background thread."""

    def __init__(self, config: ApiServerConfig, app: "SupervisorApp", logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.app = app
        self.logger = logger or logging.getLogger(__name__)
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._server:
            return

        app = self.app
        config = self.config
        logger = self.logger

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status_code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type,X-API-TOKEN")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                logger.debug("API %s - %s", self.address_string(), format % args)

            def _check_auth(self) -> bool:
                if not config.auth_token:
                    return True
                token = self.headers.get("X-API-TOKEN", "")
                if token != config.auth_token:
                    self._send_json(401, {"error": "unauthorized"})
                    return False
                return True

            def _parse_json(self) -> Optional[dict]:
                length = self.headers.get("Content-Length")
                try:
                    content_length = int(length) if length else 0
                except ValueError:
                    self._send_json(400, {"error": "bad_length"})
                    return None
                body = self.rfile.read(content_length) if content_length > 0 else b""
                try:
                    return json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "bad_json"})
                    return None

            def _parse_json_limit(self, max_bytes: int) -> Optional[dict]:
                length = self.headers.get("Content-Length")
                try:
                    content_length = int(length) if length else 0
                except ValueError:
                    self._send_json(400, {"error": "bad_length"})
                    return None
                if content_length > max_bytes:
                    self._send_json(413, {"error": "payload_too_large"})
                    return None
                body = self.rfile.read(content_length) if content_length > 0 else b""
                try:
                    return json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "bad_json"})
                    return None

            def do_OPTIONS(self) -> None:  # noqa: N802
                # CORS preflight support
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type,X-API-TOKEN")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                if not self._check_auth():
                    return
                if self.path == "/api/v1/bot/restart":
                    try:
                        response = app.restart_bot()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error restarting bot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/bot/stop":
                    try:
                        response = app.stop_bot()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error stopping bot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/bot/start":
                    try:
                        response = app.start_bot()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error starting bot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/heartbeat":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    try:
                        response = app.handle_heartbeat(payload)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error handling heartbeat: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return

                if self.path == "/api/v1/telemetry/ingest":
                    payload = self._parse_json_limit(app.config.telemetry_max_event_size_kb * 1024)
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_json"})
                        return
                    try:
                        app.ingest_telemetry_event(payload)
                        self._send_json(200, {"status": "ok"})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error ingesting telemetry: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return

                if self.path == "/api/v1/risk/evaluate":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    try:
                        response = app.evaluate_order_from_json(payload)
                        self._send_json(200, response)
                    except ValueError as exc:
                        self._send_json(400, {"error": "bad_request", "details": str(exc)})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error evaluating order: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return

                self._send_json(404, {"error": "not_found"})

            def do_GET(self) -> None:  # noqa: N802
                if not self._check_auth():
                    return
                if self.path == "/api/v1/policy/current":
                    try:
                        response = app.get_policy_payload()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building policy payload: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/policy/debug":
                    try:
                        response = app.get_policy_debug()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building policy debug payload: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/bot/status":
                    try:
                        response = app.get_bot_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building bot status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/telemetry/summary"):
                    try:
                        response = app.get_telemetry_summary()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building telemetry summary: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/telemetry/events"):
                    try:
                        limit = 200
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("limit="):
                                    try:
                                        limit = int(part.split("=", 1)[1])
                                    except ValueError:
                                        limit = 200
                        response = app.get_telemetry_events(limit=limit)
                        self._send_json(200, {"events": response})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building telemetry events: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/telemetry/alerts"):
                    try:
                        response = app.get_telemetry_alerts()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building telemetry alerts: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/status":
                    try:
                        response = app.get_status_snapshot()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/supervisor/snapshot":
                    try:
                        response = app.get_latest_snapshot_payload()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving snapshot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/overview"):
                    try:
                        response = app.dashboard_overview()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building overview: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/health"):
                    try:
                        response = app.dashboard_health()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building health: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/events"):
                    try:
                        # parse query param limit/types if present
                        limit = None
                        types = None
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("limit="):
                                    try:
                                        limit = int(part.split("=", 1)[1])
                                    except ValueError:
                                        limit = None
                                if part.startswith("types="):
                                    types = [t.strip().upper() for t in part.split("=", 1)[1].split(",") if t.strip()]
                        response = app.dashboard_events(limit=limit, types=types)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error listing events: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/tsdb/status":
                    try:
                        response = app.get_tsdb_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving TSDB status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                self._send_json(404, {"error": "not_found"})

        self._server = HTTPServer((config.host, config.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.logger.info("API server listening on %s:%s", config.host, config.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
