import subprocess
import time
import os
import signal
import sys
import pytest
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXEC = sys.executable

@pytest.fixture
def offline_env():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Add src and its subdirs to PYTHONPATH
    src_path = REPO_ROOT / "src"
    core_path = src_path / "quantum_edge_core"
    extra_paths = [
        str(REPO_ROOT / "tests"), # For sitecustomize.py
        str(REPO_ROOT),
        str(src_path),
        str(core_path),
        str(src_path / "quantum_edge_infra"),
        str(src_path / "quantum_edge_ml"),
        str(core_path / "ai_scalper_bot"),
        str(core_path / "supervisor"),
    ]
    env["PYTHONPATH"] = os.pathsep.join(extra_paths) + os.pathsep + env.get("PYTHONPATH", "")
    env["QE_OFFLINE"] = "1"
    env["QE_ROOT"] = str(REPO_ROOT)
    # Disable components that might try internet
    env["MARKET_DATA_ACCOUNT_SPOT"] = "0"
    env["MARKET_DATA_ACCOUNT_USDM"] = "0"
    env["MARKET_DATA_TSDB_ENABLED"] = "0"
    return env

def test_offline_stack_integration(offline_env):
    """
    Improved smoke test for the entire stack in offline mode.
    Verified components: MarketDataHub (MockFeed) -> Bot (Paper Mode) -> Supervisor (ZMQ Heartbeat).
    """
    hub_path = REPO_ROOT / "src/quantum_edge_core/market_data/hub.py"
    bot_path = REPO_ROOT / "src/quantum_edge_core/ai_scalper_bot/run_bot.py"
    supervisor_path = REPO_ROOT / "src/quantum_edge_core/supervisor/supervisor.py"

    processes = []
    try:
        # 1. Start Hub
        print("Starting MarketDataHub...")
        hub_proc = subprocess.Popen(
            [PYTHON_EXEC, str(hub_path)],
            env=offline_env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        processes.append(("hub", hub_proc))

        # 2. Start Bot
        print("Starting Bot...")
        bot_proc = subprocess.Popen(
            [PYTHON_EXEC, str(bot_path)],
            env=offline_env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        processes.append(("bot", bot_proc))

        # 3. Start Supervisor
        print("Starting Supervisor...")
        supervisor_proc = subprocess.Popen(
            [PYTHON_EXEC, str(supervisor_path), "run-foreground"],
            env=offline_env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        processes.append(("supervisor", supervisor_proc))

        # 4. Polling for Supervisor Health
        print("Polling Supervisor API for HEALTHY heartbeat...")
        timeout = 30
        start_time = time.time()
        healthy = False
        while time.time() - start_time < timeout:
            try:
                resp = requests.get("http://127.0.0.1:8765/api/v1/status", timeout=2)
                if resp.status_code == 200:
                    data = resp.json()
                    hb_status = data.get("heartbeat", {}).get("status")
                    if hb_status == "HEALTHY":
                        healthy = True
                        print(f"Integration HEALTHY after {int(time.time() - start_time)}s")
                        break
            except requests.exceptions.ConnectionError:
                pass

            # Check if any process died
            for name, proc in processes:
                if proc.poll() is not None:
                    _, stderr = proc.communicate()
                    pytest.fail(f"Process {name} died unexpectedly during polling! STDERR: {stderr}")

            time.sleep(2)

        assert healthy, "Supervisor heartbeat never reached HEALTHY state"

        # Give bot a bit more time to log ticks
        time.sleep(5)

        # 5. Verify Bot received data from Hub
        # We'll check the Bot's STDOUT for tick messages
        # Since we use subprocess.PIPE, we can try to read what's available
        # Note: reading from PIPE can be tricky if it's buffered, but let's try.
        # We use communicate() only at the end or use non-blocking reads.
        # For simplicity, we'll terminate and then check the full output.

    finally:
        print("Stopping all processes and collecting logs...")
        outputs = {}
        for name, proc in processes:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
            outputs[name] = (stdout, stderr)

        # 6. Final Verifications
        for name, (stdout, stderr) in outputs.items():
            print(f"--- {name} STDOUT ---")
            print(stdout)
            print(f"--- {name} STDERR ---")
            print(stderr)

        bot_stdout = outputs["bot"][0]
        bot_stderr = outputs["bot"][1]
        # Bot logs often go to stderr via logging module
        assert "DEBUG: Price=" in bot_stdout or "DEBUG: Price=" in bot_stderr, \
            "Bot did not log expected market data ticks in stdout or stderr"
        print("Verified: Bot received data from Hub.")

        supervisor_stdout = outputs["supervisor"][0]
        # Supervisor logs: "ZmqHeartbeatSubscriber connected to tcp://127.0.0.1:5557"
        # and heartbeat processing
        # Actually, heartbeat status check in step 4 already verified it received heartbeats.
