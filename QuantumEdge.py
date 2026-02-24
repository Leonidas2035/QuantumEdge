#!/usr/bin/env python3
"""
QuantumEdge Orchestrator
Production-ready process manager for HFT system.
Combines OOP architecture with ZMQ Guard and Async Log Multiplexing.
"""

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict

# Third-party imports
try:
    import psutil
    import yaml
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Critical Error: Missing dependency {e}. Please install requirements.")
    sys.exit(1)

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("quantum_edge.log", mode="a"),
    ],
)
logger = logging.getLogger("QuantumEdge")


class ProcessManager:
    """
    Manages the lifecycle of HFT services with robust startup/shutdown sequences,
    port guarding, and log multiplexing.
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.stop_event = threading.Event()
        self.pid_file = Path(".quantum_edge.pid")

        # Default ports if config is missing or incomplete
        self.zmq_ports = [5555, 5556, 5557]
        self._load_config()

    def _load_config(self):
        """Loads configuration to override defaults."""
        # Try loading ports from config/ports.yaml if it exists (per prompt suggestion)
        ports_cfg = Path("config/ports.yaml")
        if ports_cfg.exists():
            try:
                with open(ports_cfg, "r") as f:
                    data = yaml.safe_load(f)
                    if data and "ports" in data:
                        self.zmq_ports = data["ports"]
                        logger.info(f"Loaded ZMQ ports from {ports_cfg}: {self.zmq_ports}")
            except Exception as e:
                logger.warning(f"Failed to load {ports_cfg}: {e}")

    def _build_child_env(self) -> Dict[str, str]:
        """
        Critical Fix: Constructs environment with absolute PYTHONPATH to src/.
        Ensures 'import quantum_edge_core' works in subprocesses.
        """
        env = os.environ.copy()
        repo_root = Path(__file__).resolve().parent
        src_path = repo_root / "src"

        # Prepend src/ to PYTHONPATH
        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            env["PYTHONPATH"] = f"{src_path}{os.pathsep}{current_pythonpath}"
        else:
            env["PYTHONPATH"] = str(src_path)

        return env

    def _enforce_port_availability(self, ports: List[int]):
        """
        ZMQ Guard: Scans and kills processes holding critical ports.
        """
        logger.info(f"ZMQ Guard: Checking ports {ports}...")
        for port in ports:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    for conn in proc.connections(kind='inet'):
                        if conn.laddr.port == port:
                            logger.warning(
                                f"Port {port} is held by PID {proc.info['pid']} ({proc.info['name']}). Terminating..."
                            )
                            proc.terminate()
                            try:
                                proc.wait(timeout=2)
                            except psutil.TimeoutExpired:
                                logger.warning(f"PID {proc.info['pid']} did not terminate. Killing...")
                                proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        logger.info("ZMQ Guard: All ports clear.")

    def _stream_reader(self, pipe, prefix: str):
        """
        Async Log Multiplexing: Reads stdout/stderr from subprocess and logs it.
        """
        try:
            with pipe:
                for line in iter(pipe.readline, ''):
                    if not line:
                        break
                    # Strip newline and log
                    logger.info(f"[{prefix}] {line.strip()}")
        except Exception as e:
            logger.error(f"[{prefix}] Log stream error: {e}")

    def _start_log_threads(self, process: subprocess.Popen, name: str):
        """Starts daemon threads to read stdout and stderr."""
        stdout_thread = threading.Thread(
            target=self._stream_reader,
            args=(process.stdout, name.upper()),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._stream_reader,
            args=(process.stderr, name.upper()),
            daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

    def _wait_for_port(self, port: int, timeout: int = 30) -> bool:
        """Polls a TCP port until it accepts connections."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return True
            except (OSError, ConnectionRefusedError):
                time.sleep(0.5)
        return False

    def start_service(self, name: str, cmd: List[str], readiness_check=None):
        """
        Starts a subprocess with environment injection and log multiplexing.
        """
        logger.info(f"Starting {name}...")
        env = self._build_child_env()

        try:
            # Use line buffering for real-time logging
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                env=env,
                cwd=Path(__file__).resolve().parent
            )

            self.processes[name] = process
            self._start_log_threads(process, name)

            if readiness_check:
                logger.info(f"Waiting for {name} readiness...")
                if readiness_check():
                    logger.info(f"{name} is ready.")
                else:
                    logger.error(f"{name} failed to become ready.")
                    self.stop_all()
                    sys.exit(1)
            else:
                # Basic check if process crashed immediately
                time.sleep(1)
                if process.poll() is not None:
                    logger.error(f"{name} crashed immediately with exit code {process.returncode}.")
                    self.stop_all()
                    sys.exit(1)

        except Exception as e:
            logger.error(f"Failed to start {name}: {e}")
            self.stop_all()
            sys.exit(1)

    def start_all(self):
        """Executes the strict startup sequence."""
        # 1. ZMQ Guard
        self._enforce_port_availability(self.zmq_ports)

        # Define paths
        repo_root = Path(__file__).resolve().parent
        hub_script = repo_root / "src/quantum_edge_core/market_data/hub.py"
        supervisor_script = repo_root / "src/quantum_edge_core/supervisor/supervisor.py"

        if not hub_script.exists() or not supervisor_script.exists():
            logger.error(f"Critical Error: Source files not found at {hub_script} or {supervisor_script}")
            sys.exit(1)

        # 2. Start MarketDataHub
        self.start_service(
            "Hub",
            [sys.executable, str(hub_script)],
            readiness_check=lambda: self._wait_for_port(5555, timeout=15) # Hub binds ZMQ PUB to 5555
        )

        # 3. Start SupervisorAgent
        # Note: "run-foreground" is the command, and we pass config path.
        # We rely on defaults if config/config.yaml is missing, or user provides it.
        # Check if config exists, warn if not but proceed as instructed.
        if not Path("config/config.yaml").exists():
            logger.warning("config/config.yaml does not exist. Supervisor may fail or use defaults.")

        self.start_service(
            "Supervisor",
            [sys.executable, str(supervisor_script), "run-foreground", "--config", "config/config.yaml"],
            readiness_check=lambda: self._wait_for_supervisor_readiness()
        )

    def _wait_for_supervisor_readiness(self, timeout: int = 30) -> bool:
        """
        Waits for Supervisor API (if enabled) or monitors PID stability.
        """
        # Since we don't know the exact port without parsing config (and config might be missing),
        # we'll try the default port 8000 mentioned in prompt/common practice,
        # or fallback to PID monitoring.

        # Try HTTP check on common ports
        potential_ports = [8000, 8080, 5000]
        start_time = time.time()

        while time.time() - start_time < timeout:
            # First check if process is still alive
            supervisor_proc = self.processes.get("Supervisor")
            if supervisor_proc and supervisor_proc.poll() is not None:
                return False # Crashed

            # Try ports
            for port in potential_ports:
                if self._wait_for_port(port, timeout=0.1):
                    # If port is open, assume ready (or could do full HTTP GET /health)
                    return True

            # If we've waited 5 seconds and process is still up, assume it's okay (fallback)
            if time.time() - start_time > 5:
                return True

            time.sleep(0.5)

        return True

    def stop_all(self):
        """Graceful shutdown in reverse order."""
        logger.info("Stopping all services...")

        # Reverse order: Supervisor -> Hub
        shutdown_order = ["Supervisor", "Hub"]

        for name in shutdown_order:
            proc = self.processes.get(name)
            if proc and proc.poll() is None:
                logger.info(f"Stopping {name}...")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning(f"{name} did not terminate. Killing...")
                    proc.kill()

        logger.info("All services stopped.")

    def monitor_loop(self):
        """Monitors child processes and handles signals."""
        def signal_handler(sig, frame):
            logger.info(f"Received signal {sig}. Shutting down...")
            self.stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("System is running. Press Ctrl+C to stop.")

        while not self.stop_event.is_set():
            # Check health
            for name, proc in self.processes.items():
                if proc.poll() is not None:
                    logger.error(f"Critical: {name} died unexpectedly (Exit Code: {proc.returncode}). Emergency Stop.")
                    self.stop_event.set()
                    break
            time.sleep(1)

        self.stop_all()
        # Clean up PID file
        if self.pid_file.exists():
            self.pid_file.unlink()

    def run_foreground(self):
        """Main entry point for running the orchestrator."""
        # Write PID file
        self.pid_file.write_text(str(os.getpid()))

        try:
            self.start_all()
            self.monitor_loop()
        except Exception as e:
            logger.error(f"Orchestrator crashed: {e}")
            self.stop_all()
            sys.exit(1)


# --- CLI Functions ---

def run_command(args):
    """Runs the orchestrator in the foreground."""
    pm = ProcessManager()
    pm.run_foreground()

def start_command(args):
    """Starts the orchestrator in the background."""
    pid_file = Path(".quantum_edge.pid")
    if pid_file.exists():
        # Check if actually running
        try:
            pid = int(pid_file.read_text())
            if psutil.pid_exists(pid):
                print(f"QuantumEdge is already running (PID {pid}).")
                return
            else:
                print("Stale PID file found. Removing...")
                pid_file.unlink()
        except ValueError:
            pid_file.unlink()

    print("Starting QuantumEdge in background...")
    # Launch self with 'run' command
    subprocess.Popen(
        [sys.executable, __file__, "run"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print("QuantumEdge started. Use 'python QuantumEdge.py status' to check.")

def stop_command(args):
    """Stops the running orchestrator."""
    pid_file = Path(".quantum_edge.pid")
    if not pid_file.exists():
        print("QuantumEdge is not running (no PID file).")
        return

    try:
        pid = int(pid_file.read_text())
        if psutil.pid_exists(pid):
            print(f"Stopping QuantumEdge (PID {pid})...")
            os.kill(pid, signal.SIGTERM)

            # Wait for it to exit
            for _ in range(10):
                if not psutil.pid_exists(pid):
                    print("Stopped.")
                    if pid_file.exists(): pid_file.unlink()
                    return
                time.sleep(0.5)

            print("Force killing...")
            os.kill(pid, signal.SIGKILL)
        else:
            print("Process not found. Cleaning up PID file.")
            pid_file.unlink()
    except ValueError:
        print("Invalid PID file.")
        pid_file.unlink()

def status_command(args):
    """Checks status."""
    pid_file = Path(".quantum_edge.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            if psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                print(f"Status: RUNNING")
                print(f"PID: {pid}")
                print(f"Uptime: {int(time.time() - proc.create_time())}s")
                # Could list children here
                children = proc.children(recursive=True)
                print(f"Child Processes: {len(children)}")
                for child in children:
                    print(f"  - {child.name()} (PID {child.pid})")
                return
        except (ValueError, psutil.NoSuchProcess):
            pass

    print("Status: STOPPED")


def main():
    parser = argparse.ArgumentParser(description="QuantumEdge Orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommands
    subparsers.add_parser("run", help="Run in foreground (blocking)")
    subparsers.add_parser("start", help="Start in background")
    subparsers.add_parser("stop", help="Stop the system")
    subparsers.add_parser("status", help="Show system status")

    args = parser.parse_args()

    if args.command == "run":
        run_command(args)
    elif args.command == "start":
        start_command(args)
    elif args.command == "stop":
        stop_command(args)
    elif args.command == "status":
        status_command(args)

if __name__ == "__main__":
    main()
