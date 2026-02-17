#!/usr/bin/env python3
import sys
import os
import subprocess
import yaml
import logging
import logging.config
import argparse
import time
import signal
from pathlib import Path


class ProcessManager:
    def __init__(self, config_path, logging_config_path):
        self.config_path = Path(config_path)
        self.logging_config_path = Path(logging_config_path)
        self.runtime_dir = Path("runtime")
        self.runtime_dir.mkdir(exist_ok=True)
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(exist_ok=True)

        self.config = self._load_yaml(self.config_path)

        # Load services config
        self.services_config_path = self.config_path.parent / "services.yaml"
        self.services_config = {}
        if self.services_config_path.exists():
            try:
                self.services_config = self._load_yaml(self.services_config_path)
            except Exception as e:
                print(
                    f"Warning: Failed to load services config from {self.services_config_path}: {e}"
                )

        # Merge secrets if available
        secrets_path = self.config_path.parent / "secrets.yaml"
        if secrets_path.exists():
            try:
                secrets = self._load_yaml(secrets_path)
                if secrets and "env_vars" in secrets:
                    self.config.setdefault("env_vars", {}).update(secrets["env_vars"])
            except Exception as e:
                print(f"Warning: Failed to load secrets from {secrets_path}: {e}")

        self._setup_logging(self.logging_config_path)
        self.logger = logging.getLogger("QuantumEdge")

    def _load_yaml(self, path):
        if not path.exists():
            print(f"Error: Configuration file {path} not found.")
            sys.exit(1)
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _setup_logging(self, path):
        if path.exists():
            with open(path, "r") as f:
                config = yaml.safe_load(f)
                logging.config.dictConfig(config)
        else:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )

    def _get_pid_path(self, name):
        return self.runtime_dir / f"{name}.pid"

    def _get_log_path(self, name):
        return self.logs_dir / f"{name}.log"

    def _is_running(self, pid):
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def start_service(self, name, script_path):
        pid_path = self._get_pid_path(name)
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                if self._is_running(pid):
                    self.logger.info(
                        f"Service '{name}' is already running (PID: {pid})"
                    )
                    return
            except ValueError:
                pass
            pid_path.unlink()

        log_path = self._get_log_path(name)
        self.logger.info(f"Starting service '{name}' using {script_path}...")

        # Ensure we use absolute path for the script and set PYTHONPATH
        abs_script_path = str(Path(script_path).absolute())
        project_root = str(Path(__file__).parent.absolute())

        env = os.environ.copy()

        # Inject env vars from config
        config_env = self.config.get("env_vars", {})
        if config_env:
            for k, v in config_env.items():
                env[str(k)] = str(v)

        # Inject services config
        if self.services_config:
            services = self.services_config.get("services", {})
            bot_cfg = services.get("bot", {})
            sup_cfg = services.get("supervisor", {})

            if bot_cfg:
                env["QE_BOT_ID"] = str(bot_cfg.get("id", ""))
                zmq = bot_cfg.get("zmq", {})
                env["QE_BOT_TELEMETRY_PORT"] = str(zmq.get("telemetry_port", ""))
                env["QE_BOT_POLICY_PORT"] = str(zmq.get("policy_port", ""))

            if sup_cfg:
                env["QE_SUPERVISOR_ID"] = str(sup_cfg.get("id", ""))

        python_path = env.get("PYTHONPATH", "")
        src_path = Path(project_root) / "src"
        core_path = src_path / "quantum_edge_core"
        extra_paths = [
            project_root,
            str(src_path),
            str(core_path),
            str(src_path / "quantum_edge_infra"),
            str(src_path / "quantum_edge_ml"),
            str(core_path / "ai_scalper_bot"),
            str(core_path / "supervisor"),
        ]

        new_python_path = os.pathsep.join(extra_paths)
        if python_path:
            new_python_path = f"{new_python_path}{os.pathsep}{python_path}"
        env["PYTHONPATH"] = new_python_path

        # Based on instructions: All Python modules must be launched from project root.
        # We use absolute paths to avoid sys.path issues.
        try:
            with open(log_path, "a") as log_file:
                if name == "supervisor":
                    # Supervisor needs specific command and config
                    cmd = [
                        sys.executable,
                        abs_script_path,
                        "run-foreground",
                        "--config",
                        "config/config.yaml",
                    ]
                elif name == "hub":
                    # Hub usually runs without args or has internal defaults
                    cmd = [sys.executable, abs_script_path]
                elif name == "bot":
                    # Bot usually needs config
                    cmd = [
                        sys.executable,
                        abs_script_path,
                        "--config",
                        "config/config.yaml",
                    ]
                else:
                    # Fallback
                    cmd = [sys.executable, abs_script_path]

                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    cwd=project_root,
                    env=env,
                    start_new_session=True,
                )
                pid_path.write_text(str(proc.pid))
                self.logger.info(f"Service '{name}' started with PID: {proc.pid}")
        except Exception as e:
            self.logger.error(f"Failed to start service '{name}': {e}")

    def stop_service(self, name):
        pid_path = self._get_pid_path(name)
        if not pid_path.exists():
            self.logger.info(f"Service '{name}' is not running (no PID file).")
            return

        try:
            pid = int(pid_path.read_text().strip())
        except ValueError:
            self.logger.warning(f"Invalid PID file for '{name}'. Removing.")
            pid_path.unlink()
            return

        if self._is_running(pid):
            self.logger.info(f"Stopping service '{name}' (PID: {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
                # Grace period
                for _ in range(20):
                    if not self._is_running(pid):
                        break
                    time.sleep(0.5)

                if self._is_running(pid):
                    self.logger.warning(
                        f"Service '{name}' (PID: {pid}) did not terminate, killing..."
                    )
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            self.logger.info(f"Service '{name}' (PID: {pid}) is already stopped.")

        if pid_path.exists():
            pid_path.unlink()
        self.logger.info(f"Service '{name}' stopped.")

    def status(self):
        print(f"\n{'Service':<20} | {'Status':<10} | {'PID':<10}")
        print("-" * 45)
        services = {
            "supervisor": self.config.get("supervisor_path"),
            "hub": self.config.get("hub_path"),
            "bot": self.config.get("bot_path"),
        }
        for name, script_path in services.items():
            if not script_path:
                print(f"{name:<20} | NOT CONFIG | -")
                continue
            pid_path = self._get_pid_path(name)
            status = "STOPPED"
            pid_str = "-"
            if pid_path.exists():
                try:
                    pid = int(pid_path.read_text().strip())
                    if self._is_running(pid):
                        status = "RUNNING"
                        pid_str = str(pid)
                    else:
                        pid_path.unlink()
                except ValueError:
                    pid_path.unlink()
            print(f"{name:<20} | {status:<10} | {pid_str:<10}")
        print()

    def start_all(self):
        self.start_service("supervisor", self.config.get("supervisor_path"))
        self.start_service("hub", self.config.get("hub_path"))
        self.start_service("bot", self.config.get("bot_path"))

    def stop_all(self):
        # Stop in reverse order
        self.stop_service("bot")
        self.stop_service("hub")
        self.stop_service("supervisor")


def main():
    parser = argparse.ArgumentParser(description="QuantumEdge Orchestrator")
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "restart"],
        help="Action to perform",
    )
    parser.add_argument(
        "--config", default="config/system_config.yaml", help="Path to system config"
    )
    parser.add_argument(
        "--logging", default="config/logging.yaml", help="Path to logging config"
    )

    args = parser.parse_args()

    pm = ProcessManager(args.config, args.logging)

    if args.command == "start":
        pm.start_all()
    elif args.command == "stop":
        pm.stop_all()
    elif args.command == "status":
        pm.status()
    elif args.command == "restart":
        pm.stop_all()
        time.sleep(2)
        pm.start_all()


if __name__ == "__main__":
    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        main()
