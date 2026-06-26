#!/usr/bin/env python3
"""
QuantumEdge Unified Orchestrator (qe)
Manages the lifecycle of HFT components via a persistent background Daemon.
Provides high-level JSON RPC commands over ZMQ port 5560.
"""

import asyncio
import subprocess
import os
import sys
import psutil
import time
import socket
import yaml
import zmq.asyncio
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

load_dotenv()

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Orchestrator: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("quantum_edge.log", mode="a"),
    ]
)
logger = logging.getLogger("QuantumEdge")


def load_ports_config() -> dict:
    """Loads ports configuration yaml from the config directory."""
    config_path = Path("config/ports.yaml")
    if not config_path.exists():
        logger.error(f"Ports config not found at {config_path}")
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class UnifiedOrchestrator:
    """
    Asynchronous Daemon that handles port guarding, dependency checking,
    orderly bootstrapping, process telemetry collection, and graceful halts.
    """

    def __init__(self, control_port: int = 5560):
        self.context = zmq.asyncio.Context()
        self.cmd_socket = self.context.socket(zmq.REP)
        self.cmd_socket.bind(f"tcp://127.0.0.1:{control_port}")
        self.processes: Dict[str, subprocess.Popen] = {}
        self.ports_config = load_ports_config()
        self.registry = self._build_registry()
        self.pid_file = Path(".quantum_edge.pid")

    def _build_registry(self) -> Dict[str, Any]:
        """Resolves config/ports.yaml into executable commands and dependency chains."""
        registry = {}
        components = self.ports_config.get("components", {})
        for name, comp in components.items():
            module_name = comp.get("module")
            entrypoint = comp.get("entrypoint")
            args = comp.get("args", [])

            if entrypoint:
                cmd_parts = entrypoint.split()
                if cmd_parts[0].endswith(".py"):
                    cmd = [sys.executable] + cmd_parts + args
                else:
                    cmd = cmd_parts + args
            elif module_name:
                cmd = [sys.executable, "-m", module_name] + args
            else:
                logger.error(f"Invalid component configuration for {name}")
                continue

            # Resolve ports
            readiness_port = None
            ports = comp.get("ports", [])
            if ports:
                port_name = ports[0]
                readiness_port = self.ports_config.get("ports", {}).get(port_name)

            # Resolve dependency port
            dependency_port = None
            if name == "supervisor":
                dependency_port = self.ports_config.get("ports", {}).get("hub")
            elif name in ("ai_scalper", "dyndca"):
                dependency_port = self.ports_config.get("ports", {}).get("dashboard")
            elif name == "hub":
                dependency_port = self.ports_config.get("questdb", {}).get("ilp")

            registry[name] = {
                "name": name,
                "command": cmd,
                "readiness_port": readiness_port,
                "dependency_port": dependency_port,
                "startup_timeout_sec": comp.get("startup_timeout_sec", 30),
                "extra_pythonpath": comp.get("extra_pythonpath"),
                "env": comp.get("env", {})
            }
        return registry

    async def _zmq_guard_cleanup(self):
        """Scans and kills zombie processes holding critical ports."""
        ports_to_check = []
        ports_section = self.ports_config.get("ports", {})
        for p_name, p_val in ports_section.items():
            ports_to_check.append(int(p_val))

        logger.info(f"ZMQ Guard: Scanning and cleaning ports: {ports_to_check}")

        connections = []
        try:
            connections = psutil.net_connections()
        except Exception as e:
            logger.warning(f"Could not read global connections, falling back to process sweep: {e}")
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    connections.extend(proc.connections())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        killed_pids = set()
        for conn in connections:
            if conn.laddr.port in ports_to_check and conn.status == 'LISTEN':
                pid = conn.pid
                if pid and pid != os.getpid() and pid not in killed_pids:
                    try:
                        proc = psutil.Process(pid)
                        logger.warning(f"ZMQ Guard: Killing zombie process {proc.name()} (PID: {pid}) holding port {conn.laddr.port}")
                        proc.terminate()
                        killed_pids.add(pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

        if killed_pids:
            await asyncio.sleep(2)
        logger.info("ZMQ Guard: All ports checked and cleared.")

    def _build_env(self, comp_info: dict) -> Dict[str, str]:
        """Assembles absolute PYTHONPATH and CPU/Thread optimization environment variables."""
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"

        comp_env = comp_info.get("env", {})
        for k, v in comp_env.items():
            env[k] = str(v)

        project_root = Path(__file__).resolve().parent
        src_path = project_root / "src"
        hermes_path = Path("/home/korben/.hermes")

        paths = [str(src_path), str(hermes_path)]
        extra_path = comp_info.get("extra_pythonpath")
        if extra_path:
            paths.append(extra_path)

        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            env["PYTHONPATH"] = f"{os.pathsep.join(paths)}{os.pathsep}{current_pythonpath}"
        else:
            env["PYTHONPATH"] = os.pathsep.join(paths)

        return env

    async def _wait_for_tcp_port(self, port: int, timeout: int) -> bool:
        """Asynchronous Readiness-probe verifying network port availability."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                await asyncio.sleep(0.5)
        return False

    async def launch_module(self, comp_info: dict) -> bool:
        """Launches an isolated child process, handles dependency chains, and monitors status."""
        name = comp_info["name"]
        dep_port = comp_info["dependency_port"]
        timeout = comp_info["startup_timeout_sec"]

        if dep_port:
            logger.info(f"Waiting for dependency port {dep_port} before starting {name}...")
            if not await self._wait_for_tcp_port(dep_port, timeout):
                logger.error(f"Dependency check failed: port {dep_port} is not ready. Aborting start of {name}.")
                return False

        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{name}.log"

        log_file = open(log_path, "a", encoding="utf-8")
        env = self._build_env(comp_info)
        cmd = comp_info["command"]

        logger.info(f"Launching component {name} with command {cmd}...")
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parent)
            )
            self.processes[name] = proc

            await asyncio.sleep(1.0)
            if proc.poll() is not None:
                logger.error(f"Component {name} crashed immediately on startup with return code {proc.returncode}.")
                return False

            ready_port = comp_info["readiness_port"]
            if ready_port:
                logger.info(f"Waiting for readiness on port {ready_port} for {name}...")
                if not await self._wait_for_tcp_port(ready_port, timeout):
                    logger.error(f"Readiness probe failed for {name} on port {ready_port}. Terminating component...")
                    proc.terminate()
                    return False

            logger.info(f"Component {name} is successfully started and ready (PID: {proc.pid}).")
            return True
        except Exception as e:
            logger.error(f"Failed to spawn component {name}: {e}")
            return False

    async def bootstrap_full_system(self) -> dict:
        """Performs bottom-up initialization sequence based on topological dependency layers."""
        await self._zmq_guard_cleanup()

        results = {}
        startup_order = ["hub", "supervisor", "ai_scalper", "dyndca", "lockbotbtc"]
        for name in startup_order:
            if name not in self.registry:
                continue
            success = await self.launch_module(self.registry[name])
            results[name] = "RUNNING" if success else "FAILED"
            if not success:
                logger.error(f"Cascading bootstrap failed at component '{name}'. Halting bootstrap.")
                break
        return results

    async def generate_telemetry_report(self) -> dict:
        """Gathers system usage statistics (CPU, RAM, PID, Status) for running processes."""
        report = {}
        for name, proc in list(self.processes.items()):
            is_running = proc.poll() is None
            cpu_usage, memory_mb = 0.0, 0.0
            if is_running:
                try:
                    p = psutil.Process(proc.pid)
                    cpu_usage = p.cpu_percent(interval=0.1)
                    memory_mb = round(p.memory_info().rss / (1024 * 1024), 2)
                except psutil.NoSuchProcess:
                    is_running = False

            report[name] = {
                "state": "RUNNING" if is_running else "TERMINATED",
                "pid": proc.pid if is_running else None,
                "cpu_percent": cpu_usage,
                "ram_mb": memory_mb
            }
        return report

    async def halt_all_system(self):
        """Halts all modules in reverse dependency order."""
        logger.info("Halting all components...")
        shutdown_order = ["lockbotbtc", "dyndca", "ai_scalper", "supervisor", "hub"]
        for name in shutdown_order:
            proc = self.processes.get(name)
            if proc and proc.poll() is None:
                logger.info(f"Terminating process tree for component {name} (PID: {proc.pid})...")
                try:
                    parent = psutil.Process(proc.pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    if children:
                        psutil.wait_procs(children, timeout=3)
                    parent.terminate()
                    try:
                        parent.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        parent.kill()
                except psutil.NoSuchProcess:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                logger.info(f"Component {name} halted.")
        self.processes.clear()

    async def control_loop(self):
        """Listens on 5560 for JSON-RPC management requests."""
        logger.info("Unified Orchestrator Daemon activated. Listening on port 5560...")
        self.pid_file.write_text(str(os.getpid()))

        try:
            while True:
                request = await self.cmd_socket.recv_json()
                action = request.get("action")
                logger.info(f"Received daemon directive: {action}")

                if action == "SYSTEM_BOOTSTRAP":
                    status_matrix = await self.bootstrap_full_system()
                    await self.cmd_socket.send_json({"status": "SUCCESS", "matrix": status_matrix})
                elif action == "SYSTEM_DIAGNOSTICS":
                    report = await self.generate_telemetry_report()
                    await self.cmd_socket.send_json({"status": "SUCCESS", "telemetry": report})
                elif action == "SYSTEM_HALT":
                    await self.halt_all_system()
                    await self.cmd_socket.send_json({"status": "SUCCESS", "message": "All executive planes halted."})
                    break
                else:
                    await self.cmd_socket.send_json({"status": "ERROR", "message": f"Unknown directive: {action}"})
        except Exception as e:
            logger.error(f"Exception in control loop: {e}")
        finally:
            if self.pid_file.exists():
                self.pid_file.unlink()
            logger.info("Orchestrator Daemon stopped.")


# --- Client Commands ---

def send_daemon_command(action: str, payload: dict = None) -> Optional[dict]:
    """Helper method to send a synchronous JSON-RPC request to the Daemon."""
    import zmq
    context = zmq.Context()
    socket_client = context.socket(zmq.REQ)
    socket_client.setsockopt(zmq.RCVTIMEO, 30000)
    socket_client.setsockopt(zmq.SNDTIMEO, 30000)
    try:
        socket_client.connect("tcp://127.0.0.1:5560")
        request = {"action": action}
        if payload:
            request.update(payload)
        socket_client.send_json(request)
        return socket_client.recv_json()
    except Exception as e:
        logger.error(f"Failed to communicate with Orchestrator Daemon: {e}")
        return None


def run_command(args):
    """Starts the Orchestrator Daemon in the foreground."""
    daemon = UnifiedOrchestrator()
    try:
        asyncio.run(daemon.control_loop())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")


def start_command(args):
    """Launches the Orchestrator Daemon in the background and requests bootstrap."""
    pid_file = Path(".quantum_edge.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            if psutil.pid_exists(pid):
                print(f"Orchestrator Daemon is already running (PID: {pid}).")
                return
            else:
                pid_file.unlink()
        except ValueError:
            pid_file.unlink()

    print("Starting Orchestrator Daemon in background...")
    subprocess.Popen(
        [sys.executable, __file__, "run"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    print("Waiting for Daemon to bind to port 5560...")
    daemon_ready = False
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            with socket.create_connection(("127.0.0.1", 5560), timeout=1):
                daemon_ready = True
                break
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.5)

    if not daemon_ready:
        print("Error: Orchestrator Daemon failed to start on port 5560.")
        return

    print("Daemon is active. Sending SYSTEM_BOOTSTRAP command...")
    res = send_daemon_command("SYSTEM_BOOTSTRAP")
    if res and res.get("status") == "SUCCESS":
        print("Bootstrap Complete. Components status:")
        matrix = res.get("matrix", {})
        for k, v in matrix.items():
            print(f"  - {k}: {v}")
    else:
        print(f"Bootstrap failed: {res}")


def stop_command(args):
    """Sends SYSTEM_HALT to shut down the running Daemon and all components."""
    print("Sending SYSTEM_HALT to Orchestrator Daemon...")
    res = send_daemon_command("SYSTEM_HALT")
    if res and res.get("status") == "SUCCESS":
        print("System successfully halted.")
    else:
        print(f"Failed to halt system (or daemon was not running): {res}")


def status_command(args):
    """Queries and displays daemon process telemetry."""
    res = send_daemon_command("SYSTEM_DIAGNOSTICS")
    if res and res.get("status") == "SUCCESS":
        print("QuantumEdge System Status: RUNNING")
        telemetry = res.get("telemetry", {})
        for comp, stats in telemetry.items():
            print(f"Component: {comp}")
            print(f"  State: {stats.get('state')}")
            print(f"  PID: {stats.get('pid')}")
            print(f"  CPU Usage: {stats.get('cpu_percent')}%")
            print(f"  RAM Usage: {stats.get('ram_mb')} MB")
    else:
        print("QuantumEdge System Status: STOPPED (Daemon not reachable on 5560)")


def dashboard_command(args):
    """Starts the Single Pane of Glass dashboard in the background."""
    print("Starting QuantumEdge Dashboard on port 8501...")

    try:
        import streamlit
    except ImportError:
        print("Streamlit not found. Please install requirements first.")
        return

    dashboard_path = (
        Path(__file__).resolve().parent
        / "src"
        / "quantum_edge_core"
        / "dashboard"
        / "app.py"
    )
    if not dashboard_path.exists():
        print(f"Error: Dashboard app not found at {dashboard_path}")
        return

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard_path),
        "--server.port",
        "8501",
        "--server.headless",
        "true",
    ]

    env = os.environ.copy()
    project_root = Path(__file__).resolve().parent
    src_path = project_root / "src"
    current_pythonpath = env.get("PYTHONPATH", "")
    if current_pythonpath:
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{current_pythonpath}"
    else:
        env["PYTHONPATH"] = str(src_path)

    subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    print("Dashboard is running in the background. Visit http://localhost:8501")


def main():
    parser = argparse.ArgumentParser(description="QuantumEdge Unified Orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Run Orchestrator Daemon in foreground")
    subparsers.add_parser("start", help="Start Daemon in background and bootstrap")
    subparsers.add_parser("stop", help="Halt system and daemon")
    subparsers.add_parser("status", help="Get system status report")
    subparsers.add_parser("dashboard", help="Start Streamlit UI dashboard")

    args = parser.parse_args()

    if args.command == "run":
        run_command(args)
    elif args.command == "start":
        start_command(args)
    elif args.command == "stop":
        stop_command(args)
    elif args.command == "status":
        status_command(args)
    elif args.command == "dashboard":
        dashboard_command(args)


if __name__ == "__main__":
    main()
