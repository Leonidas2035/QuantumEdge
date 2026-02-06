#!/usr/bin/env python3
"""
Unified UAT System Runner for QuantumEdge AI Scalper.
Automates Port Cleanup, Mock Environment, and Bot Execution.
"""
import subprocess
import time
import os
import signal
import sys
import threading
from typing import IO

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

MOCK_SCRIPT = "scripts/verify_bot_isolation.py"
BOT_SCRIPT = "src/quantum_edge_core/ai_scalper_bot/run_bot.py"
ENV_VARS = os.environ.copy()
ENV_VARS["PYTHONPATH"] = f"{os.getcwd()}/src:{os.getcwd()}"
# Ensure Bot runs in Dry Run mode
ENV_VARS["DRY_RUN"] = "True"
ENV_VARS["PYTHONUNBUFFERED"] = "1"

def kill_port(port: int):
    """Force kill any process using the specified port."""
    try:
        # Use fuser to find and kill
        subprocess.run(["fuser", "-k", f"{port}/tcp"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except Exception:
        pass

def stream_reader(pipe: IO, prefix: str, color: str, stop_event: threading.Event, success_flags: dict):
    """Reads stdout from a subprocess and prints with prefix."""
    try:
        for line in iter(pipe.readline, ''):
            if stop_event.is_set():
                break
            if not line:
                break
            
            decoded = line.strip()
            if not decoded:
                continue
                
            print(f"{color}{prefix} | {decoded}{RESET}")
            
            # Check for Success Conditions
            if "!!! SIGNAL:" in decoded or "!!! EXECUTE:" in decoded: # Bot trading
                success_flags["bot_signal"] = True
            if "✅ [HEARTBEAT]" in decoded: # Supervisor receiving
                success_flags["heartbeat"] = True
                
    except Exception:
        pass

def main():
    print(f"{YELLOW}>>> QuantumEdge UAT System Runner <<<{RESET}")
    
    # 1. Cleanup
    print(f"{BLUE}[SYSTEM] Cleaning ports 5555 and 5557...{RESET}")
    kill_port(5555)
    kill_port(5557)
    time.sleep(1)
    
    success_flags = {"bot_signal": False, "heartbeat": False}
    stop_event = threading.Event()
    
    processes = []
    
    try:
        # 2. Start Mock Environment (Market + Supervisor)
        print(f"{BLUE}[SYSTEM] Starting Mock Environment ({MOCK_SCRIPT})...{RESET}")
        mock_proc = subprocess.Popen(
            [sys.executable, MOCK_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=ENV_VARS
        )
        processes.append(mock_proc)
        
        t_mock = threading.Thread(target=stream_reader, args=(mock_proc.stdout, "[MOCK]", YELLOW, stop_event, success_flags))
        t_mock.daemon = True
        t_mock.start()
        
        # Wait for Mock to bind
        time.sleep(2)
        
        # 3. Start Bot
        print(f"{BLUE}[SYSTEM] Starting Bot Engine ({BOT_SCRIPT})...{RESET}")
        bot_proc = subprocess.Popen(
            [sys.executable, BOT_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=ENV_VARS
        )
        processes.append(bot_proc)
        
        t_bot = threading.Thread(target=stream_reader, args=(bot_proc.stdout, "[BOT ]", GREEN, stop_event, success_flags))
        t_bot.daemon = True
        t_bot.start()
        
        print(f"{BLUE}[SYSTEM] Monitoring for triggers... (Press Ctrl+C to stop){RESET}")
        
        # Monitor Loop
        start_time = time.time()
        while True:
            time.sleep(1)
            
            if success_flags["bot_signal"] and success_flags["heartbeat"]:
                print(f"\n{GREEN}✅ SYSTEM INTEGRATION TEST PASSED!{RESET}")
                print(f"{GREEN}   - Bot is generating signals/execution.{RESET}")
                print(f"{GREEN}   - Supervisor is receiving heartbeats via ZMQ.{RESET}")
                break
                
            if time.time() - start_time > 30: # Timeout 30s
                print(f"\n{RED}❌ TIMEOUT: Success triggers not detected in 30s.{RESET}")
                break
                
            # Check if processes died
            if mock_proc.poll() is not None:
                print(f"{RED}❌ Mock Process died unexpectedly.{RESET}")
                break
            if bot_proc.poll() is not None:
                print(f"{RED}❌ Bot Process died unexpectedly.{RESET}")
                break

    except KeyboardInterrupt:
        print(f"\n{YELLOW}[SYSTEM] Stopping...{RESET}")
    finally:
        stop_event.set()
        print(f"{BLUE}[SYSTEM] Terminating subprocesses...{RESET}")
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
        
        # Final cleanup just in case
        kill_port(5555)
        kill_port(5557)
        print(f"{BLUE}[SYSTEM] Done.{RESET}")

if __name__ == "__main__":
    main()
