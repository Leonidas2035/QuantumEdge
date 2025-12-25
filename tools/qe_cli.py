import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from qe_config import get_qe_config, get_qe_paths


def _build_env(overrides: Optional[dict[str, str]] = None) -> dict:
    env = os.environ.copy()
    paths = get_qe_paths()
    config = get_qe_config()

    env.setdefault("QE_ROOT", str(paths["qe_root"]))
    env.setdefault("QE_CONFIG_DIR", str(paths["config_dir"]))
    env.setdefault("QE_RUNTIME_DIR", str(paths["runtime_dir"]))
    env.setdefault("QE_LOGS_DIR", str(paths["logs_dir"]))
    env.setdefault("QE_DATA_DIR", str(paths["data_dir"]))
    env.setdefault("SUPERVISOR_HOST", config["supervisor"]["host"])
    env.setdefault("SUPERVISOR_PORT", str(config["supervisor"]["port"]))
    env.setdefault("SUPERVISOR_URL", config["supervisor"]["url"])

    py_paths = [
        str(paths["qe_root"]),
        str(paths["bot_dir"]),
        str(paths["supervisor_dir"]),
        str(paths["meta_agent_dir"]),
    ]
    if env.get("PYTHONPATH"):
        py_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(py_paths)

    if overrides:
        for key, value in overrides.items():
            env[key] = value
    return env


def _resolve_path(value: Optional[str], default_rel: str) -> Path:
    if value:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = get_qe_paths()["qe_root"] / candidate
        return candidate.resolve()
    return (get_qe_paths()["qe_root"] / default_rel).resolve()


def _run_target(target: Path, extra_args: List[str], env_overrides: Optional[dict[str, str]] = None) -> int:
    if not target.exists():
        print(f"[qe_cli] Missing target: {target}", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(target)] + extra_args
    return subprocess.call(cmd, env=_build_env(env_overrides), cwd=str(get_qe_paths()["qe_root"]))


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _scan_for_secret_files(root: Path) -> List[Path]:
    suspicious: List[Path] = []
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


def _run_diag() -> int:
    paths = get_qe_paths()
    config = get_qe_config()
    supervisor = config["supervisor"]

    print("QuantumEdge diag")
    print("===============")
    print(f"QE_ROOT: {paths['qe_root']}")
    print(f"QE_CONFIG_DIR: {paths['config_dir']}")
    print(f"QE_RUNTIME_DIR: {paths['runtime_dir']}")
    print(f"QE_LOGS_DIR: {paths['logs_dir']}")
    print(f"QE_DATA_DIR: {paths['data_dir']}")
    print(f"Supervisor: {supervisor['url']}")

    required = [
        paths["config_dir"] / "quantumedge.yaml",
        paths["config_dir"] / "paths.yaml",
        paths["config_dir"] / "supervisor.yaml",
        paths["config_dir"] / "bot.yaml",
        paths["config_dir"] / "meta_agent.yaml",
        paths["config_dir"] / "env.example",
    ]

    failures = 0
    for cfg in required:
        if cfg.exists():
            print(f"[OK] Config: {cfg}")
        else:
            print(f"[FAIL] Missing config: {cfg}")
            failures += 1

    port_ok = _port_available(supervisor["host"], supervisor["port"])
    status = "available" if port_ok else "in use"
    print(f"[CHECK] Port {supervisor['host']}:{supervisor['port']} is {status}")
    if not port_ok:
        failures += 1

    secrets = _scan_for_secret_files(paths["qe_root"])
    if secrets:
        print("[FAIL] Suspicious secret files found:")
        for path in secrets:
            print(f"  - {path}")
        failures += 1
    else:
        print("[OK] No secret files found by name scan.")

    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantumEdge CLI wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sup = subparsers.add_parser("supervisor")
    sup.add_argument("--config", dest="config_path", help="Path to supervisor config YAML.")
    sup.add_argument("args", nargs=argparse.REMAINDER)

    bot = subparsers.add_parser("bot")
    bot.add_argument("--config", dest="config_path", help="Path to bot config YAML.")
    bot.add_argument("args", nargs=argparse.REMAINDER)

    meta = subparsers.add_parser("meta")
    meta.add_argument("--config", dest="config_path", help="Path to meta-agent config YAML.")
    meta.add_argument("args", nargs=argparse.REMAINDER)

    subparsers.add_parser("diag")

    legacy = {
        "supervisor-foreground": "supervisor",
        "bot-run": "bot",
        "meta-run": "meta",
    }
    for name in legacy:
        leg = subparsers.add_parser(name)
        leg.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    extra = getattr(args, "args", [])
    if extra and extra[0] == "--":
        extra = extra[1:]

    paths = get_qe_paths()
    if args.command == "diag":
        return _run_diag()

    if args.command in legacy:
        mapped = legacy[args.command]
        args.command = mapped

    if args.command == "supervisor":
        cfg_path = _resolve_path(getattr(args, "config_path", None), "config/supervisor.yaml")
        env_overrides = {"SUPERVISOR_CONFIG": str(cfg_path)}
        sup_args = extra or ["run-foreground"]
        sup_args = ["--config", str(cfg_path)] + sup_args
        return _run_target(paths["supervisor_dir"] / "supervisor.py", sup_args, env_overrides)

    if args.command == "bot":
        cfg_path = _resolve_path(getattr(args, "config_path", None), "config/bot.yaml")
        env_overrides = {"QE_CONFIG_PATH": str(cfg_path)}
        return _run_target(paths["bot_dir"] / "run_bot.py", extra, env_overrides)

    if args.command == "meta":
        cfg_path = _resolve_path(getattr(args, "config_path", None), "config/meta_agent.yaml")
        env_overrides = {"META_AGENT_CONFIG": str(cfg_path)}
        meta_args = ["--config", str(cfg_path)] + extra
        return _run_target(paths["meta_agent_dir"] / "meta_agent.py", meta_args, env_overrides)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
