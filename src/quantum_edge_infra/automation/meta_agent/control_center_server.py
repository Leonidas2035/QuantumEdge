import json
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from approval_engine import ApprovalError
from control_center import (approve_apply_run, create_task_inbox,
                            ensure_active_project, get_run_detail, list_inbox,
                            list_runs, list_schedules_with_state,
                            set_active_project)
from logger import configure_logger
from projects_registry import load_projects_registry


def _resolve_base_dir() -> str:
    base = os.path.abspath(os.path.dirname(__file__))
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    parent = os.path.abspath(os.path.join(base, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(
        os.path.join(parent, "ai_scalper_bot")
    ):
        return parent
    return base


def _resolve_runtime_dir() -> str:
    base_abs = os.path.abspath(_resolve_base_dir())
    env_runtime = os.getenv("META_AGENT_RUNTIME_DIR") or os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        candidate = os.path.abspath(env_runtime)
        try:
            if os.path.commonpath([candidate, base_abs]) == base_abs:
                return candidate
        except ValueError:
            pass
    return os.path.abspath(os.path.join(base_abs, "runtime"))


def _ui_dir() -> str:
    return os.path.join(_resolve_base_dir(), "ui")


def _schedules_dir() -> str:
    runtime_dir = _resolve_runtime_dir()
    candidate = os.path.join(runtime_dir, "schedules")
    if os.path.isdir(candidate):
        return candidate
    return os.path.join(_resolve_base_dir(), "schedules")


def validate_token(provided: str | None, expected: str) -> bool:
    return bool(provided) and provided == expected


def _content_type(path: str) -> str:
    if path.endswith(".css"):
        return "text/css"
    if path.endswith(".js"):
        return "application/javascript"
    if path.endswith(".html"):
        return "text/html"
    return "application/octet-stream"


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ControlCenterHandler(BaseHTTPRequestHandler):
    server_version = "MetaAgentControlCenter/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(
        self, status: int, content: str, content_type: str = "text/plain"
    ) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _check_token(self) -> bool:
        token = self.headers.get("X-CC-Token")
        if not validate_token(token, self.server.cc_token):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return False
        return True

    def _serve_static(self, path: str) -> None:
        ui_root = _ui_dir()
        if path in {"/", "/index.html"}:
            index_path = os.path.join(ui_root, "index.html")
            if not os.path.exists(index_path):
                self._send_text(HTTPStatus.NOT_FOUND, "index.html missing")
                return
            with open(index_path, "r", encoding="utf-8") as handle:
                content = handle.read()
            content = content.replace("__CC_TOKEN__", self.server.cc_token)
            self._send_text(HTTPStatus.OK, content, "text/html")
            return

        rel = path.lstrip("/")
        file_path = os.path.abspath(os.path.join(ui_root, rel))
        if os.path.commonpath([file_path, ui_root]) != ui_root:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not os.path.exists(file_path):
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return
        with open(file_path, "rb") as handle:
            data = handle.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _content_type(file_path))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._check_token():
                return
            return self._handle_api_get(parsed)
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._check_token():
                return
            return self._handle_api_post(parsed)
        self._send_text(HTTPStatus.NOT_FOUND, "not found")

    def _handle_api_get(self, parsed) -> None:
        runtime_dir = _resolve_runtime_dir()
        if parsed.path == "/api/health":
            ok, checks = _health_check(runtime_dir)
            self._send_json(HTTPStatus.OK, {"ok": ok, "checks": checks})
            return
        if parsed.path == "/api/status":
            projects = load_projects_registry()
            active_project = ensure_active_project(projects, runtime_dir)
            sched = list_schedules_with_state(_schedules_dir(), runtime_dir)
            runs = list_runs(runtime_dir, limit=5)
            base_dir = _resolve_base_dir()
            self._send_json(
                HTTPStatus.OK,
                {
                    "active_project": active_project,
                    "projects": [
                        {
                            "id": p.project_id,
                            "label": p.label,
                            "root": p.root,
                            "root_exists": (
                                os.path.isdir(p.root)
                                if os.path.isabs(p.root)
                                else os.path.isdir(os.path.join(base_dir, p.root))
                            ),
                        }
                        for p in projects
                    ],
                    "recent_runs": runs,
                    "scheduler": sched,
                },
            )
            return
        if parsed.path == "/api/runs":
            qs = parse_qs(parsed.query)
            limit = int(qs.get("limit", [50])[0])
            verdict = qs.get("verdict", ["any"])[0]
            runs = list_runs(runtime_dir, limit=limit, verdict=verdict)
            self._send_json(HTTPStatus.OK, {"runs": runs})
            return
        if parsed.path.startswith("/api/run/") and parsed.path.endswith("/patch"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid patch request"})
            return
        if parsed.path.startswith("/api/run/") and "/patch/" in parsed.path:
            parts = parsed.path.split("/")
            run_id = parts[3]
            name = "/".join(parts[5:])
            return self._send_patch(run_id, name)
        if parsed.path.startswith("/api/run/") and "/gate/" in parsed.path:
            parts = parsed.path.split("/")
            run_id = parts[3]
            name = "/".join(parts[5:])
            return self._send_gate(run_id, name)
        if parsed.path.startswith("/api/run/"):
            run_id = parsed.path.split("/")[3]
            try:
                detail = get_run_detail(run_id, runtime_dir)
            except FileNotFoundError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            self._send_json(HTTPStatus.OK, detail)
            return
        if parsed.path == "/api/inbox":
            self._send_json(HTTPStatus.OK, {"tasks": list_inbox(runtime_dir)})
            return
        if parsed.path == "/api/schedules":
            schedules = list_schedules_with_state(_schedules_dir(), runtime_dir)
            self._send_json(HTTPStatus.OK, {"schedules": schedules})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_api_post(self, parsed) -> None:
        runtime_dir = _resolve_runtime_dir()
        if parsed.path == "/api/tasks":
            payload = self._read_json()
            try:
                result = create_task_inbox(payload, runtime_dir)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, result)
            return
        if parsed.path.startswith("/api/run/") and parsed.path.endswith(
            "/approve-apply"
        ):
            run_id = parsed.path.split("/")[3]
            try:
                result = approve_apply_run(run_id, runtime_dir)
            except ApprovalError as exc:
                self._send_json(
                    exc.status_code, {"error": str(exc), "exit_code": exc.exit_code}
                )
                return
            self._send_json(HTTPStatus.OK, result)
            return
        if parsed.path.startswith("/api/schedule/") and parsed.path.endswith("/toggle"):
            schedule_id = parsed.path.split("/")[3]
            payload = self._read_json()
            enabled = bool(payload.get("enabled"))
            try:
                from control_center import toggle_schedule

                toggle_schedule(schedule_id, enabled, _schedules_dir())
            except FileNotFoundError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "schedule not found"})
                return
            self._send_json(
                HTTPStatus.OK, {"schedule_id": schedule_id, "enabled": enabled}
            )
            return
        if parsed.path == "/api/active-project":
            payload = self._read_json()
            project_id = str(payload.get("project_id") or "")
            if project_id:
                set_active_project(project_id, runtime_dir)
            self._send_json(HTTPStatus.OK, {"active_project": project_id})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _send_patch(self, run_id: str, name: str) -> None:
        runtime_dir = _resolve_runtime_dir()
        patches_dir = os.path.join(runtime_dir, "runs", run_id, "patches")
        safe_name = unquote(name)
        target = os.path.abspath(os.path.join(patches_dir, safe_name))
        if os.path.commonpath([target, patches_dir]) != patches_dir:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not os.path.exists(target):
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return
        with open(target, "r", encoding="utf-8") as handle:
            data = handle.read()
        self._send_text(HTTPStatus.OK, data, "text/plain")

    def _send_gate(self, run_id: str, name: str) -> None:
        runtime_dir = _resolve_runtime_dir()
        safe_name = unquote(name)
        if safe_name.startswith("approval/"):
            rel = safe_name.split("/", 1)[1]
            gates_dir = os.path.join(runtime_dir, "runs", run_id, "approval", "gates")
        else:
            rel = safe_name
            gates_dir = os.path.join(runtime_dir, "runs", run_id, "gates")
        target = os.path.abspath(os.path.join(gates_dir, rel))
        if os.path.commonpath([target, gates_dir]) != gates_dir:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if not os.path.exists(target):
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return
        with open(target, "r", encoding="utf-8") as handle:
            data = handle.read()
        self._send_text(HTTPStatus.OK, data, "text/plain")


def _health_check(runtime_dir: str) -> tuple[bool, list[dict]]:
    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        os.makedirs(runtime_dir, exist_ok=True)
        record("runtime_dir", True, runtime_dir)
    except Exception as exc:
        record("runtime_dir", False, str(exc))

    for sub in ("logs", "runs"):
        path = os.path.join(runtime_dir, sub)
        try:
            os.makedirs(path, exist_ok=True)
            record(f"runtime_{sub}", True, path)
        except Exception as exc:
            record(f"runtime_{sub}", False, str(exc))

    try:
        from safety_policy import load_safety_policy

        load_safety_policy()
        record("safety_policy", True, "loaded")
    except Exception as exc:
        record("safety_policy", False, str(exc))

    try:
        from write_engine import apply_change_set_with_policy  # noqa: F401

        record("write_engine", True, "available")
    except Exception as exc:
        record("write_engine", False, str(exc))

    ok = all(item["ok"] for item in checks)
    return ok, checks


def run_server(
    bind: str, port: int, token: Optional[str] = None
) -> tuple[str, int, str]:
    runtime_dir = _resolve_runtime_dir()
    log_level = (os.getenv("META_AGENT_LOG_LEVEL") or "INFO").upper()
    logger = configure_logger(
        "meta_agent.control_center",
        runtime_dir,
        log_level,
        log_filename="control_center.log",
    )

    cc_token = token or secrets.token_urlsafe(16)
    server = ThreadedHTTPServer((bind, port), ControlCenterHandler)
    server.cc_token = cc_token
    server.logger = logger
    return server, cc_token
