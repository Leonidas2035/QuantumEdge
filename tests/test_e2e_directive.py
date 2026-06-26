import subprocess
import time
import zmq
import yaml
import pathlib
import pytest
import asyncio
from unittest.mock import patch, MagicMock

# Dynamically find bridge port from ports.yaml
def load_bridge_port() -> int:
    bridge_port = 5562
    try:
        current_path = pathlib.Path(__file__).resolve()
        for parent in current_path.parents:
            ports_file = parent / "config" / "ports.yaml"
            if ports_file.exists():
                with open(ports_file, "r") as f:
                    ports_data = yaml.safe_load(f)
                    bridge_port = ports_data.get("ports", {}).get("bridge_command", 5562)
                break
    except Exception:
        pass
    return bridge_port

@pytest.mark.asyncio
async def test_e2e_directive_transmission():
    from quantum_edge_core.ai_scalper_bot.run_bot import BotEngine
    
    # 1. Initialize BotEngine with real ZMQ but mocked streams/gateway to avoid crashes
    with patch("quantum_edge_core.ai_scalper_bot.run_bot.ZmqSubStream") as mock_stream, \
         patch("quantum_edge_core.ai_scalper_bot.run_bot.SupervisorReporter") as mock_reporter, \
         patch("quantum_edge_core.ai_scalper_bot.run_bot.QuestDbTelemetry") as mock_quest:
         
         bot = BotEngine()
         assert bot.cmd_sub is not None
         
         # 2. Wait for ZMQ setup
         time.sleep(0.5)
         
         # 3. Trigger CLI command to send a directive
         python_executable = ".venv/bin/python"
         cmd = [
             python_executable,
             "hermes_agent/zmq_mcp_bridge.py",
             "directive",
             "--bot", "ai_scalper_bot",
             "--command-type", "LIMIT_BUY",
             "--price", "62000.0",
             "--qty", "0.05",
             "--reason", "Test E2E directive transmission"
         ]
         
         # Start the bridge command process
         proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
         
         # 4. Check for incoming message on bot's socket in a non-blocking poll
         received_cmd = None
         start_time = time.time()
         while time.time() - start_time < 5.0:
             try:
                 # Check if message is ready on bot's subscriber
                 raw_parts = bot.cmd_sub.recv_multipart(zmq.NOBLOCK)
                 import json
                 if len(raw_parts) >= 2:
                     msg = raw_parts[-1]
                     received_cmd = json.loads(msg.decode("utf-8"))
                     break
             except zmq.Again:
                 await asyncio.sleep(0.1)
                 
         # Terminate bridge process if it hasn't finished
         proc.terminate()
         stdout, stderr = proc.communicate()
         
         print(f"Bridge stdout: {stdout.decode()}")
         print(f"Bridge stderr: {stderr.decode()}")
         
         # 5. Assertions
         assert received_cmd is not None, "Failed to receive ZMQ directive from bridge!"
         assert received_cmd.get("command_type") == "LIMIT_BUY"
         assert received_cmd.get("price") == 62000.0
         assert received_cmd.get("quantity") == 0.05
         assert received_cmd.get("target_bot") == "ai_scalper_bot"
         
         # Clean up bot sockets
         bot.cmd_sub.close()
         bot.zmq_ctx.term()
