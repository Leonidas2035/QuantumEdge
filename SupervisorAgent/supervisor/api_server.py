"""Lightweight HTTP API exposing heartbeat and risk evaluation."""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import time
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor import SupervisorApp  # type: ignore

from supervisor.events import new_trace_id
from supervisor.security import check_dashboard_auth, dashboard_auth_mode, dashboard_auth_token, is_path_allowed


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
        static_dir = Path(__file__).resolve().parent.parent / "static"

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status_code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type,X-API-TOKEN,Authorization")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self._log_api_call(status_code)

            def _init_trace(self) -> None:
                self._trace_id = new_trace_id()
                self._start_ts = time.time()
                self._method = self.command

            def _log_api_call(self, status_code: int) -> None:
                path = self.path.split("?", 1)[0]
                if not path.startswith("/api/"):
                    return
                trace_id = getattr(self, "_trace_id", None)
                start_ts = getattr(self, "_start_ts", None)
                duration_ms = int((time.time() - start_ts) * 1000) if start_ts else 0
                try:
                    app.log_api_call(self._method, path, status_code, duration_ms, trace_id)
                except Exception:
                    pass

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                logger.debug("API %s - %s", self.address_string(), format % args)

            def _check_auth(self, require_dashboard_token: bool) -> bool:
                if config.auth_token:
                    token = self.headers.get("X-API-TOKEN", "")
                    if token != config.auth_token:
                        self._send_json(401, {"error": "unauthorized"})
                        return False
                if require_dashboard_token:
                    mode = dashboard_auth_mode()
                    token = dashboard_auth_token() or ""
                    if not check_dashboard_auth(dict(self.headers), mode, token):
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

            def _serve_static(self, rel_path: str) -> None:
                if not rel_path:
                    rel_path = "index.html"
                target = (static_dir / rel_path).resolve()
                if not is_path_allowed(target, static_dir) or not target.exists() or not target.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                    content_type = f"{content_type}; charset=utf-8"
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                # CORS preflight support
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type,X-API-TOKEN,Authorization")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                self._init_trace()
                if not self._check_auth(require_dashboard_token=True):
                    return
                if self.path.startswith("/api/v1/process/"):
                    path_base = self.path.split("?", 1)[0]
                    parts = path_base.strip("/").split("/")
                    if len(parts) == 5:
                        _, _, _, name, action = parts
                        try:
                            if action == "start":
                                if app.process_manager.is_running_named(name):
                                    self._send_json(409, {"error": "already_running", "process": name})
                                    return
                                response = app.start_process(name)
                                self._send_json(200, {"status": "started", "process": response})
                                return
                            if action == "stop":
                                response = app.stop_process(name)
                                self._send_json(200, {"status": "stopped", "process": response})
                                return
                            if action == "restart":
                                response = app.restart_process(name)
                                self._send_json(200, {"status": "restarted", "process": response})
                                return
                        except KeyError:
                            self._send_json(404, {"error": "not_found", "process": name})
                            return
                        except Exception as exc:  # pylint: disable=broad-except
                            logger.exception("Error managing process %s: %s", name, exc)
                            self._send_json(500, {"error": "internal_error"})
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

                if self.path == "/api/v1/telemetry/trade_result":
                    payload = self._parse_json_limit(app.config.telemetry_max_event_size_kb * 1024)
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_json"})
                        return
                    try:
                        response = app.ingest_trade_result(payload)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error ingesting trade result: %s", exc)
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
                if self.path == "/api/v1/autopilot/enable":
                    try:
                        response = app.autopilot_set_enabled(True)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error enabling autopilot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/autopilot/disable":
                    try:
                        response = app.autopilot_set_enabled(False)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error disabling autopilot: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/autopilot/target_state":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    target_state = payload.get("state") if isinstance(payload, dict) else None
                    if not target_state:
                        self._send_json(400, {"error": "missing_state"})
                        return
                    try:
                        response = app.autopilot_set_target_state(str(target_state))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error setting target state: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/policy/rollout":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    symbol = payload.get("symbol") if isinstance(payload, dict) else None
                    path = payload.get("path") if isinstance(payload, dict) else None
                    if not path:
                        self._send_json(400, {"error": "missing_path"})
                        return
                    try:
                        response = app.policy_rollout_payload(symbol, str(path))
                        self._send_json(200, response)
                    except ValueError as exc:
                        self._send_json(400, {"error": "bad_request", "details": str(exc)})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error rolling out policy: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/policy/rollback":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    symbol = payload.get("symbol") if isinstance(payload, dict) else None
                    try:
                        response = app.policy_rollback_payload(symbol)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error rolling back policy: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/alerts/ack":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    alert_id = payload.get("alert_id") if isinstance(payload, dict) else None
                    note = payload.get("note") if isinstance(payload, dict) else ""
                    if not alert_id:
                        self._send_json(400, {"error": "missing_alert_id"})
                        return
                    try:
                        response = app.alerts_ack(str(alert_id), str(note or ""))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error acknowledging alert: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/alerts/silence":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    rule = payload.get("rule") if isinstance(payload, dict) else None
                    minutes = payload.get("minutes") if isinstance(payload, dict) else 60
                    if not rule:
                        self._send_json(400, {"error": "missing_rule"})
                        return
                    try:
                        response = app.alerts_silence(str(rule), int(minutes))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error silencing alert: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/safety/kill_switch":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    enabled = payload.get("enabled") if isinstance(payload, dict) else None
                    challenge_id = payload.get("challenge_id") if isinstance(payload, dict) else None
                    if challenge_id is None or enabled is None:
                        self._send_json(400, {"error": "missing_fields"})
                        return
                    try:
                        response = app.apply_kill_switch(bool(enabled), str(challenge_id))
                        self._send_json(200, response)
                    except ValueError as exc:
                        self._send_json(400, {"error": "bad_request", "details": str(exc)})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error applying kill switch: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/cmd":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_payload"})
                        return
                    cmd = payload.get("cmd")
                    if not cmd:
                        self._send_json(400, {"error": "missing_cmd"})
                        return
                    cmd_payload = payload.get("payload")
                    if not isinstance(cmd_payload, dict):
                        cmd_payload = {k: v for k, v in payload.items() if k != "cmd"}
                    try:
                        response = app.lockbot_send_cmd(str(cmd), cmd_payload)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error sending lockbot cmd: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/execution/arm":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_payload"})
                        return
                    mode = payload.get("mode") or "DRY_RUN"
                    ttl_s = payload.get("ttl_s") or 0
                    reason = payload.get("reason") or ""
                    if not ttl_s:
                        self._send_json(400, {"error": "missing_ttl"})
                        return
                    try:
                        response = app.lockbot_execution_arm(str(mode), int(ttl_s), str(reason))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error arming lockbot execution: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/execution/disarm":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_payload"})
                        return
                    reason = payload.get("reason") or ""
                    try:
                        response = app.lockbot_execution_disarm(str(reason))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error disarming lockbot execution: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/execution/cancel-all":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    if not isinstance(payload, dict):
                        self._send_json(400, {"error": "bad_payload"})
                        return
                    scope = payload.get("scope") or "OPEN_ONLY"
                    reason = payload.get("reason") or ""
                    try:
                        response = app.lockbot_execution_cancel_all(str(scope), str(reason))
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error canceling lockbot orders: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/policy/enable":
                    payload = self._parse_json()
                    if payload is None:
                        return
                    enabled = True
                    if isinstance(payload, dict) and "enabled" in payload:
                        enabled = bool(payload.get("enabled"))
                    try:
                        response = app.lockbot_policy_set_enabled(enabled)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error toggling lockbot policy: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/dashboard/reset-counters":
                    try:
                        response = app.dashboard_reset_counters()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error resetting dashboard counters: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return

                self._send_json(404, {"error": "not_found"})

            def do_GET(self) -> None:  # noqa: N802
                self._init_trace()
                path = self.path.split("?", 1)[0]
                if path in {"/", "/dashboard", "/dashboard/"}:
                    self._serve_static("index.html")
                    return
                if path.startswith("/static/"):
                    self._serve_static(path[len("/static/") :])
                    return
                if not self._check_auth(require_dashboard_token=False):
                    return
                if self.path.startswith("/api/v1/policy/current"):
                    if "scope=ml" in self.path or "type=ml" in self.path:
                        symbol = "BTCUSDT"
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("symbol="):
                                    symbol = part.split("=", 1)[1]
                        response = app.policy_list_payload(symbol)
                        self._send_json(200, response)
                        return
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
                if self.path == "/api/v1/system/status":
                    try:
                        response = app.get_system_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building system status: %s", exc)
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
                if self.path == "/api/v1/lockbot/btc/status":
                    try:
                        response = app.lockbot_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building lockbot status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/execution/status":
                    try:
                        limit = 20
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("limit="):
                                    try:
                                        limit = int(part.split("=", 1)[1])
                                    except ValueError:
                                        limit = 20
                        response = app.lockbot_execution_status(limit)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building lockbot execution status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/lockbot/btc/policy/status":
                    try:
                        response = app.lockbot_policy_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building lockbot policy status: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/lockbot/btc/policy/decisions"):
                    limit = 20
                    if "?" in self.path:
                        _, query = self.path.split("?", 1)
                        for part in query.split("&"):
                            if part.startswith("limit="):
                                try:
                                    limit = int(part.split("=", 1)[1])
                                except ValueError:
                                    limit = 20
                    try:
                        response = app.lockbot_policy_decisions(limit)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building lockbot policy decisions: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/events/tail"):
                    try:
                        limit = 200
                        types = None
                        since_ts_ms = None
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("limit="):
                                    try:
                                        limit = int(part.split("=", 1)[1])
                                    except ValueError:
                                        limit = 200
                                if part.startswith("types="):
                                    types = [t.strip() for t in part.split("=", 1)[1].split(",") if t.strip()]
                                if part.startswith("since_ts_ms="):
                                    try:
                                        since_ts_ms = int(part.split("=", 1)[1])
                                    except ValueError:
                                        since_ts_ms = None
                        response = app.get_events_tail(limit=limit, types=types, since_ts_ms=since_ts_ms)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error tailing events: %s", exc)
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
                if self.path == "/api/v1/autopilot/status":
                    try:
                        response = app.autopilot_status()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building autopilot status: %s", exc)
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
                if self.path.startswith("/api/v1/dashboard/strategies"):
                    try:
                        response = app.dashboard_strategies()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building strategies: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/performance"):
                    try:
                        response = app.dashboard_performance()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building performance: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/alerts"):
                    try:
                        response = app.dashboard_alerts()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building alerts: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/audit"):
                    try:
                        limit = 200
                        since_ts_ms = None
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("limit="):
                                    try:
                                        limit = int(part.split("=", 1)[1])
                                    except ValueError:
                                        limit = 200
                                if part.startswith("since_ts_ms="):
                                    try:
                                        since_ts_ms = int(part.split("=", 1)[1])
                                    except ValueError:
                                        since_ts_ms = None
                        response = app.dashboard_audit(since_ts_ms=since_ts_ms, limit=limit)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building audit list: %s", exc)
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
                if self.path.startswith("/api/v1/dashboard/summary"):
                    try:
                        symbol = "BTCUSDT"
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("symbol="):
                                    symbol = part.split("=", 1)[1]
                        response = app.dashboard_summary(symbol)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building dashboard summary: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/timeseries"):
                    import urllib.parse as _urlparse

                    params = {"metric": "", "symbol": "BTCUSDT", "from": "", "to": "", "bucket": "10s"}
                    if "?" in self.path:
                        _, query = self.path.split("?", 1)
                        for part in query.split("&"):
                            if "=" not in part:
                                continue
                            key, value = part.split("=", 1)
                            if key in params:
                                params[key] = _urlparse.unquote(value)
                    try:
                        response = app.tsdb_timeseries(
                            params["metric"],
                            params["symbol"],
                            params["from"],
                            params["to"],
                            params["bucket"],
                        )
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building dashboard timeseries: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/events/recent"):
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
                        response = app.dashboard_events(limit=limit, types=None)
                        self._send_json(200, {"events": response})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building dashboard events: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/dashboard/audit/recent"):
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
                        response = app.audit_recent(limit=limit)
                        self._send_json(200, {"items": response})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building audit list: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/alerts/active"):
                    try:
                        response = app.alerts_snapshot()
                        self._send_json(200, {"active": response.get("active", [])})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building alerts: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/alerts/recent"):
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
                        response = app.alerts_snapshot()
                        items = response.get("recent", [])[-limit:]
                        self._send_json(200, {"items": items})
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error building alerts: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/policy/list"):
                    try:
                        symbol = "BTCUSDT"
                        if "?" in self.path:
                            _, query = self.path.split("?", 1)
                            for part in query.split("&"):
                                if part.startswith("symbol="):
                                    symbol = part.split("=", 1)[1]
                        response = app.policy_list_payload(symbol)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error listing policy: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path == "/api/v1/safety/kill_switch":
                    try:
                        response = app.get_kill_switch_challenge()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error creating kill switch challenge: %s", exc)
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
                if self.path == "/api/v1/tsdb/health":
                    try:
                        response = app.get_tsdb_health()
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving TSDB health: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/tsdb/metrics/latest"):
                    import urllib.parse as _urlparse

                    symbol = "BTCUSDT"
                    if "?" in self.path:
                        _, query = self.path.split("?", 1)
                        for part in query.split("&"):
                            if part.startswith("symbol="):
                                symbol = _urlparse.unquote(part.split("=", 1)[1])
                    try:
                        response = app.tsdb_latest_metrics(symbol)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving TSDB metrics: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/tsdb/events/recent"):
                    import urllib.parse as _urlparse

                    symbol = "BTCUSDT"
                    limit = 200
                    if "?" in self.path:
                        _, query = self.path.split("?", 1)
                        for part in query.split("&"):
                            if part.startswith("symbol="):
                                symbol = _urlparse.unquote(part.split("=", 1)[1])
                            if part.startswith("limit="):
                                try:
                                    limit = int(part.split("=", 1)[1])
                                except ValueError:
                                    limit = 200
                    try:
                        response = app.tsdb_recent_events(symbol, limit=limit)
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving TSDB events: %s", exc)
                        self._send_json(500, {"error": "internal_error"})
                    return
                if self.path.startswith("/api/v1/tsdb/timeseries"):
                    import urllib.parse as _urlparse

                    params = {"metric": "", "symbol": "BTCUSDT", "from": "", "to": "", "bucket": "10s"}
                    if "?" in self.path:
                        _, query = self.path.split("?", 1)
                        for part in query.split("&"):
                            if "=" not in part:
                                continue
                            key, value = part.split("=", 1)
                            if key in params:
                                params[key] = _urlparse.unquote(value)
                    try:
                        response = app.tsdb_timeseries(
                            params["metric"],
                            params["symbol"],
                            params["from"],
                            params["to"],
                            params["bucket"],
                        )
                        self._send_json(200, response)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error retrieving TSDB timeseries: %s", exc)
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
