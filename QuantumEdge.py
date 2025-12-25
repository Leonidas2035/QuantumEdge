from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.qe_config import load_config_file
from tools.qe_doctor import run_doctor
from tools.qe_paths import ensure_dirs, get_paths


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs(paths: dict) -> None:
    ensure_dirs(paths)


def _pid_path(paths: dict, name: str) -> Path:
    return Path(paths["runtime_dir"]) / f"{name}.pid"


def _log_path(paths: dict, name: str) -> Path:
    return Path(paths["logs_dir"]) / f"{name}.log"


def _state_path(paths: dict) -> Path:
    return Path(paths["runtime_dir"]) / "quantumedge.state.json"


def _read_pid(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    return value if value > 0 else None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(f"{pid}\n", encoding="utf-8")


def _remove_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        success = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        if not success:
            return False
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process(pid: int, timeout: int, logger: logging.Logger) -> bool:
    if not _is_process_alive(pid):
        return True
    if os.name == "nt":
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            logger.info("Sent CTRL_BREAK to PID %s", pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("CTRL_BREAK failed for %s: %s", pid, exc)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to PID %s", pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SIGTERM failed for %s: %s", pid, exc)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_process_alive(pid):
            return True
        time.sleep(0.5)

    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Forced terminate PID %s", pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Forced terminate failed for %s: %s", pid, exc)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("Forced kill PID %s", pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Forced kill failed for %s: %s", pid, exc)
    return not _is_process_alive(pid)


def _spawn_process(
    name: str,
    args: list[str],
    cwd: Path,
    env: dict,
    log_path: Path,
    logger: logging.Logger,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
        )
    logger.info("Spawned %s PID=%s", name, proc.pid)
    return proc.pid


def _resolve_cli_path(value: Optional[str], default_rel: str, qe_root: Path) -> Path:
    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = qe_root / candidate
        return candidate.resolve()
    return (qe_root / default_rel).resolve()


def _resolve_config_path(value: Optional[str], env_var: str, default_rel: str, qe_root: Path) -> Path:
    if value:
        return _resolve_cli_path(value, default_rel, qe_root)
    env_value = os.getenv(env_var)
    if env_value:
        return _resolve_cli_path(env_value, default_rel, qe_root)
    return (qe_root / default_rel).resolve()


def _load_global_config(config_path: Path, logger: logging.Logger) -> dict:
    if not config_path.exists():
        logger.warning("Global config not found: %s", config_path)
        return {}
    try:
        return load_config_file(config_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load global config %s: %s", config_path, exc)
        return {}


def _extract_supervisor_settings(global_config: dict) -> dict:
    supervisor_cfg = global_config.get("supervisor", {}) if isinstance(global_config.get("supervisor"), dict) else {}
    env_host = os.getenv("SUPERVISOR_HOST") or os.getenv("QE_SUPERVISOR_HOST")
    env_port = os.getenv("SUPERVISOR_PORT") or os.getenv("QE_SUPERVISOR_PORT")
    env_url = os.getenv("SUPERVISOR_URL")

    host = env_host or supervisor_cfg.get("host") or "127.0.0.1"
    port_raw = env_port or supervisor_cfg.get("port", 8765)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8765
    url = env_url or supervisor_cfg.get("url") or f"http://{host}:{port}"
    return {"host": str(host), "port": int(port), "url": str(url)}


def _extract_orchestrator_config(global_config: dict) -> dict:
    orch_cfg = global_config.get("orchestrator", {}) if isinstance(global_config.get("orchestrator"), dict) else {}
    supervisor_spawns_bot = orch_cfg.get("supervisor_spawns_bot", True)
    health_path = orch_cfg.get("supervisor_health_path", "/api/v1/dashboard/health")
    fallback_paths = orch_cfg.get("supervisor_health_fallbacks", ["/api/v1/status", "/"])
    timeout = orch_cfg.get("startup_timeout_s", 30)
    interval = orch_cfg.get("poll_interval_s", 1)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 1.0
    if isinstance(fallback_paths, str):
        fallback_paths = [fallback_paths]
    if not isinstance(fallback_paths, list):
        fallback_paths = ["/api/v1/status", "/"]
    return {
        "supervisor_spawns_bot": bool(supervisor_spawns_bot),
        "health_path": str(health_path),
        "health_fallbacks": [str(path) for path in fallback_paths],
        "startup_timeout_s": timeout,
        "poll_interval_s": interval,
    }


def _load_optional_config(config_path: Path, logger: logging.Logger) -> dict:
    if not config_path.exists():
        return {}
    try:
        raw = load_config_file(config_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to read config %s: %s", config_path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _get_supervisor_health_path(supervisor_config: dict, default_path: str) -> str:
    value = supervisor_config.get("health_path") if isinstance(supervisor_config, dict) else None
    return str(value) if value else default_path


def _get_supervisor_spawn_flag(supervisor_config: dict) -> Optional[bool]:
    if isinstance(supervisor_config, dict) and "supervisor_spawns_bot" in supervisor_config:
        return bool(supervisor_config["supervisor_spawns_bot"])
    return None


def _resolve_bot_management(supervisor_config: dict, orchestrator_settings: dict) -> bool:
    managed = bool(orchestrator_settings.get("supervisor_spawns_bot", True))
    supervisor_flag = _get_supervisor_spawn_flag(supervisor_config)
    if supervisor_flag is not None:
        managed = supervisor_flag
    return managed


def _build_env(paths: dict, supervisor_settings: dict, config_paths: dict) -> dict:
    env = os.environ.copy()
    env.setdefault("QE_ROOT", str(paths["qe_root"]))
    env.setdefault("QE_CONFIG_DIR", str(paths["config_dir"]))
    env.setdefault("QE_RUNTIME_DIR", str(paths["runtime_dir"]))
    env.setdefault("QE_LOGS_DIR", str(paths["logs_dir"]))
    env.setdefault("QE_DATA_DIR", str(paths["data_dir"]))
    env.setdefault("QE_ARTIFACTS_DIR", str(paths.get("artifacts_dir", Path(paths["qe_root"]) / "artifacts")))
    env.setdefault("SUPERVISOR_HOST", supervisor_settings["host"])
    env.setdefault("SUPERVISOR_PORT", str(supervisor_settings["port"]))
    env.setdefault("SUPERVISOR_URL", supervisor_settings["url"])
    env.setdefault("SUPERVISOR_CONFIG", str(config_paths["supervisor"]))
    env.setdefault("QE_CONFIG_PATH", str(config_paths["bot"]))
    env.setdefault("BOT_CONFIG", str(config_paths["bot"]))
    env.setdefault("META_AGENT_CONFIG", str(config_paths["meta"]))

    py_paths = [
        str(paths["qe_root"]),
        str(paths["bot_dir"]),
        str(paths["supervisor_dir"]),
        str(paths["meta_agent_dir"]),
    ]
    if env.get("PYTHONPATH"):
        py_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_paths)
    return env


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _probe_url(url: str, timeout: float = 3.0) -> Optional[int]:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status
    except HTTPError as exc:
        return exc.code
    except URLError:
        return None


def _fetch_json(url: str, logger: logging.Logger, timeout: float = 3.0) -> Optional[dict]:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status < 200 or resp.status >= 300:
                return None
            payload = resp.read()
            if not payload:
                return None
            return json.loads(payload.decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
    return None


def _wait_for_http_ready(
    base_url: str,
    paths: Iterable[str],
    timeout: int,
    interval: float,
    logger: logging.Logger,
) -> Optional[str]:
    deadline = time.monotonic() + timeout
    path_list = [path for path in paths if path]
    while time.monotonic() < deadline:
        for path in path_list:
            url = _join_url(base_url, path)
            status = _probe_url(url)
            if status is not None:
                logger.info("Supervisor ready at %s (status %s)", url, status)
                return path
        time.sleep(interval)
    return None


def _get_supervisor_bot_state(supervisor_url: str, logger: logging.Logger) -> tuple[str, Optional[int]]:
    api_url = _join_url(supervisor_url, "/api/v1/bot/status")
    payload = _fetch_json(api_url, logger)
    if payload and isinstance(payload, dict) and payload.get("state"):
        state = str(payload.get("state", "UNKNOWN")).upper()
        pid = payload.get("pid") if isinstance(payload.get("pid"), int) else None
        return state, pid
    status_url = _join_url(supervisor_url, "/api/v1/status")
    payload = _fetch_json(status_url, logger)
    if not payload or not isinstance(payload.get("bot"), dict):
        return "UNKNOWN", None
    bot_info = payload["bot"]
    running = bot_info.get("running")
    pid = bot_info.get("pid") if isinstance(bot_info.get("pid"), int) else None
    if running is True:
        return "RUNNING", pid
    if running is False:
        return "STOPPED", pid
    return "UNKNOWN", pid


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _scan_for_secret_files(root: Path) -> list[Path]:
    suspicious: list[Path] = []
    ignore_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "logs", "runtime", "data"}
    ignore_suffixes = {".env.example", ".env.sample", ".env.template"}
    for dirpath, dirnames, filenames in os.walk(root):
        for dirname in list(dirnames):
            if dirname in ignore_dirs:
                dirnames.remove(dirname)
                continue
            if dirname.lower() in {"secrets", "backup_secrets"}:
                suspicious.append(Path(dirpath) / dirname)
                dirnames.remove(dirname)
        for filename in filenames:
            lower = filename.lower()
            if any(lower.endswith(sfx) for sfx in ignore_suffixes):
                continue
            if lower in {"secrets.env", ".env"} or lower.endswith(".env") or lower.endswith(".enc"):
                suspicious.append(Path(dirpath) / filename)
                continue
            if lower in {"secrets", "backup_secrets"}:
                suspicious.append(Path(dirpath) / filename)
    return suspicious


def _write_state(
    paths: dict,
    config_paths: dict,
    supervisor_settings: dict,
    processes: dict,
) -> None:
    state = {
        "updated_at": _now_iso(),
        "supervisor_url": supervisor_settings["url"],
        "configs": {name: str(path) for name, path in config_paths.items()},
        "processes": processes,
    }
    _state_path(paths).write_text(json.dumps(state, indent=2), encoding="utf-8")


def _status_from_pid(pid: Optional[int]) -> dict:
    if not pid:
        return {"pid": None, "running": False}
    return {"pid": pid, "running": _is_process_alive(pid)}


def _configure_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("quantumedge")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    buffer: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            buffer.append(line)
    return "".join(buffer)


def _print_status(
    paths: dict,
    config_paths: dict,
    supervisor_settings: dict,
    orchestrator_settings: dict,
    supervisor_config: dict,
    logger: logging.Logger,
) -> int:
    supervisor_pid = _read_pid(_pid_path(paths, "supervisor"))
    bot_pid = _read_pid(_pid_path(paths, "bot"))
    meta_pid = _read_pid(_pid_path(paths, "meta"))
    managed_by_supervisor = _resolve_bot_management(supervisor_config, orchestrator_settings)

    print("QuantumEdge status")
    print("==================")
    print(f"QE_ROOT: {paths['qe_root']}")
    print(f"Supervisor URL: {supervisor_settings['url']}")
    print(f"Configs:")
    for name, path in config_paths.items():
        print(f"  - {name}: {path}")
    print("Processes:")
    supervisor_status = _status_from_pid(supervisor_pid)
    print(f"  - supervisor: pid={supervisor_status['pid']} running={supervisor_status['running']}")

    if managed_by_supervisor:
        if supervisor_status["running"]:
            state, remote_pid = _get_supervisor_bot_state(supervisor_settings["url"], logger)
        else:
            state, remote_pid = "UNKNOWN", None
        pid_note = f" pid={remote_pid}" if remote_pid else ""
        print(f"  - bot: managed_by_supervisor=True state={state}{pid_note}")
    else:
        bot_status = _status_from_pid(bot_pid)
        print(f"  - bot: managed_by_supervisor=False pid={bot_status['pid']} running={bot_status['running']}")

    meta_status = _status_from_pid(meta_pid)
    print(f"  - meta: pid={meta_status['pid']} running={meta_status['running']}")
    return 0


def _run_diag(
    paths: dict,
    config_paths: dict,
    supervisor_settings: dict,
    orchestrator_settings: dict,
    supervisor_config: dict,
) -> int:
    print("QuantumEdge diag")
    print("===============")
    return run_doctor(json_output=False)


def _stop_all(
    paths: dict,
    logger: logging.Logger,
    supervisor_first: bool = False,
) -> int:
    order = ["supervisor", "bot", "meta"] if supervisor_first else ["meta", "bot", "supervisor"]
    failures = 0
    for name in order:
        pid_file = _pid_path(paths, name)
        pid = _read_pid(pid_file)
        if not pid:
            _remove_pid(pid_file)
            continue
        logger.info("Stopping %s (PID %s)", name, pid)
        if _terminate_process(pid, timeout=15, logger=logger):
            _remove_pid(pid_file)
            logger.info("Stopped %s", name)
        else:
            logger.warning("Failed to stop %s (PID %s)", name, pid)
            failures += 1
    return 0 if failures == 0 else 1


def _start_services(
    paths: dict,
    config_paths: dict,
    supervisor_settings: dict,
    orchestrator_settings: dict,
    supervisor_config: dict,
    with_meta: bool,
    supervisor_spawns_bot_override: Optional[bool],
    logger: logging.Logger,
) -> int:
    _ensure_dirs(paths)
    env = _build_env(paths, supervisor_settings, config_paths)

    supervisor_pid_file = _pid_path(paths, "supervisor")
    supervisor_started = False
    existing_supervisor_pid = _read_pid(supervisor_pid_file)
    if existing_supervisor_pid and _is_process_alive(existing_supervisor_pid):
        logger.info("Supervisor already running (PID %s)", existing_supervisor_pid)
    else:
        if existing_supervisor_pid:
            _remove_pid(supervisor_pid_file)
        sup_args = [
            sys.executable,
            str(Path(paths["supervisor_dir"]) / "supervisor.py"),
            "--config",
            str(config_paths["supervisor"]),
            "run-foreground",
        ]
        supervisor_pid = _spawn_process(
            "supervisor",
            sup_args,
            Path(paths["qe_root"]),
            env,
            _log_path(paths, "supervisor"),
            logger,
        )
        _write_pid(supervisor_pid_file, supervisor_pid)
        supervisor_started = True

    health_paths = [
        orchestrator_settings["health_path"],
        *orchestrator_settings["health_fallbacks"],
    ]
    primary_health_url = _join_url(supervisor_settings["url"], health_paths[0])
    logger.info("Waiting for supervisor health: %s", primary_health_url)
    if len(health_paths) > 1:
        fallback_urls = [_join_url(supervisor_settings["url"], path) for path in health_paths[1:]]
        logger.info("Health fallbacks: %s", ", ".join(fallback_urls))
    ready_path = _wait_for_http_ready(
        supervisor_settings["url"],
        health_paths,
        orchestrator_settings["startup_timeout_s"],
        orchestrator_settings["poll_interval_s"],
        logger,
    )
    if not ready_path:
        logger.error("Supervisor health check timed out")
        log_tail = _tail_file(_log_path(paths, "supervisor"), 80)
        if log_tail:
            logger.error("Supervisor log tail:\n%s", log_tail)
        else:
            logger.error("No supervisor log data. Try: python SupervisorAgent/supervisor.py run-foreground")
        if supervisor_started:
            pid = _read_pid(supervisor_pid_file)
            if pid:
                _terminate_process(pid, timeout=10, logger=logger)
            _remove_pid(supervisor_pid_file)
        return 1

    supervisor_spawns_bot = orchestrator_settings["supervisor_spawns_bot"]
    supervisor_flag = _get_supervisor_spawn_flag(supervisor_config)
    if supervisor_flag is not None:
        supervisor_spawns_bot = supervisor_flag
    if supervisor_spawns_bot_override is not None:
        supervisor_spawns_bot = supervisor_spawns_bot_override

    bot_pid_file = _pid_path(paths, "bot")
    if supervisor_spawns_bot:
        _remove_pid(bot_pid_file)
        logger.info("Supervisor manages the bot (no direct spawn).")
    else:
        existing_bot_pid = _read_pid(bot_pid_file)
        if existing_bot_pid and _is_process_alive(existing_bot_pid):
            logger.info("Bot already running (PID %s)", existing_bot_pid)
        else:
            if existing_bot_pid:
                _remove_pid(bot_pid_file)
            bot_args = [sys.executable, str(Path(paths["bot_dir"]) / "run_bot.py")]
            bot_pid = _spawn_process(
                "bot",
                bot_args,
                Path(paths["qe_root"]),
                env,
                _log_path(paths, "bot"),
                logger,
            )
            _write_pid(bot_pid_file, bot_pid)

    meta_pid_file = _pid_path(paths, "meta")
    if with_meta:
        existing_meta_pid = _read_pid(meta_pid_file)
        if existing_meta_pid and _is_process_alive(existing_meta_pid):
            logger.info("Meta-agent already running (PID %s)", existing_meta_pid)
        else:
            if existing_meta_pid:
                _remove_pid(meta_pid_file)
            meta_args = [
                sys.executable,
                str(Path(paths["meta_agent_dir"]) / "meta_agent.py"),
                "--config",
                str(config_paths["meta"]),
            ]
            meta_pid = _spawn_process(
                "meta",
                meta_args,
                Path(paths["qe_root"]),
                env,
                _log_path(paths, "meta"),
                logger,
            )
            _write_pid(meta_pid_file, meta_pid)
    else:
        _remove_pid(meta_pid_file)

    processes = {
        "supervisor": _status_from_pid(_read_pid(supervisor_pid_file)),
        "bot": _status_from_pid(_read_pid(bot_pid_file)),
        "meta": _status_from_pid(_read_pid(meta_pid_file)),
    }
    _write_state(paths, config_paths, supervisor_settings, processes)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantumEdge orchestrator")
    parser.add_argument("--config", dest="global_config", help="Path to global quantumedge.yaml.")
    parser.add_argument("--supervisor-config", dest="supervisor_config", help="Path to supervisor.yaml.")
    parser.add_argument("--bot-config", dest="bot_config", help="Path to bot.yaml.")
    parser.add_argument("--meta-config", dest="meta_config", help="Path to meta_agent.yaml.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--with-meta", action="store_true", help="Start meta-agent.")
    start_parser.add_argument(
        "--no-supervisor-bot",
        action="store_true",
        help="Spawn bot directly instead of Supervisor spawning it.",
    )
    subparsers.add_parser("stop")
    restart_parser = subparsers.add_parser("restart")
    restart_parser.add_argument("--with-meta", action="store_true", help="Start meta-agent.")
    restart_parser.add_argument(
        "--no-supervisor-bot",
        action="store_true",
        help="Spawn bot directly instead of Supervisor spawning it.",
    )
    subparsers.add_parser("status")
    diag_parser = subparsers.add_parser("diag")
    diag_parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--name", choices=["supervisor", "bot", "meta", "quantumedge"], help="Log name.")
    logs_parser.add_argument("--lines", type=int, default=200, help="Lines to tail.")

    args = parser.parse_args()

    paths = get_paths()
    _ensure_dirs(paths)

    qe_root = Path(paths["qe_root"])
    global_config_path = _resolve_cli_path(args.global_config, "config/quantumedge.yaml", qe_root)
    config_paths = {
        "global": global_config_path,
        "supervisor": _resolve_config_path(args.supervisor_config, "SUPERVISOR_CONFIG", "config/supervisor.yaml", qe_root),
        "bot": _resolve_config_path(args.bot_config, "QE_CONFIG_PATH", "config/bot.yaml", qe_root),
        "meta": _resolve_config_path(args.meta_config, "META_AGENT_CONFIG", "config/meta_agent.yaml", qe_root),
    }

    logger = _configure_logger(_log_path(paths, "quantumedge"))
    global_config = _load_global_config(global_config_path, logger)
    supervisor_settings = _extract_supervisor_settings(global_config)
    orchestrator_settings = _extract_orchestrator_config(global_config)
    supervisor_config = _load_optional_config(Path(config_paths["supervisor"]), logger)
    orchestrator_settings["health_path"] = _get_supervisor_health_path(
        supervisor_config,
        orchestrator_settings["health_path"],
    )

    if args.command == "diag":
        if args.json:
            return run_doctor(json_output=True)
        return _run_diag(paths, config_paths, supervisor_settings, orchestrator_settings, supervisor_config)

    if args.command == "status":
        return _print_status(paths, config_paths, supervisor_settings, orchestrator_settings, supervisor_config, logger)

    if args.command == "logs":
        name = args.name or "quantumedge"
        log_target = _log_path(paths, name)
        output = _tail_file(log_target, max(1, args.lines))
        if not output:
            print(f"No log data for {name} ({log_target}).")
            return 0
        print(output, end="")
        return 0

    if args.command == "stop":
        result = _stop_all(paths, logger)
        processes = {
            "supervisor": _status_from_pid(_read_pid(_pid_path(paths, "supervisor"))),
            "bot": _status_from_pid(_read_pid(_pid_path(paths, "bot"))),
            "meta": _status_from_pid(_read_pid(_pid_path(paths, "meta"))),
        }
        _write_state(paths, config_paths, supervisor_settings, processes)
        return result

    if args.command == "restart":
        _stop_all(paths, logger)
        return _start_services(
            paths,
            config_paths,
            supervisor_settings,
            orchestrator_settings,
            supervisor_config,
            args.with_meta,
            False if args.no_supervisor_bot else None,
            logger,
        )

    if args.command == "start":
        return _start_services(
            paths,
            config_paths,
            supervisor_settings,
            orchestrator_settings,
            supervisor_config,
            args.with_meta,
            False if args.no_supervisor_bot else None,
            logger,
        )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
