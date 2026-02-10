#!/usr/bin/env python3
"""
QuantumEdge Live Demo Runner.
Orchestrates Real-Time BingX Data Feed and AI Scalper Bot.
"""

import subprocess
import time
import os
import sys
import threading
from typing import IO
import contextlib

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

FEED_SCRIPT = "scripts/bingx_feed.py"
BOT_SCRIPT = "src/quantum_edge_core/ai_scalper_bot/run_bot.py"

ENV_VARS = os.environ.copy()
ENV_VARS["PYTHONPATH"] = f"{os.getcwd()}/src:{os.getcwd()}"
ENV_VARS["PYTHONUNBUFFERED"] = "1"
# Ensure Bot runs in Live/Demo Mode?
# Config has 'dry_run': False, but 'use_sandbox': True in config.py.
# run_bot.py uses Config() class.


def kill_port(port: int):
    """Force kill any process using the specified port."""
    with contextlib.suppress(Exception):
        subprocess.run(
            ["fuser", "-k", f"{port}/tcp"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )


def stream_reader(pipe: IO, prefix: str, color: str):
    """Reads stdout from a subprocess and prints with prefix."""
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            decoded = line.strip()
            if not decoded:
                continue
            print(f"{color}{prefix} | {decoded}{RESET}")
    except Exception:
        pass


def main():
    print(f"{YELLOW}>>> QuantumEdge LIVE DEMO (BingX VST) <<<{RESET}")

    # 1. Cleanup
    print(f"{BLUE}[SYSTEM] Cleaning ports 5555 and 5557...{RESET}")
    kill_port(5555)
    kill_port(5557)
    time.sleep(1)

    processes = []

    try:
        # 2. Start Live Data Feed
        print(f"{BLUE}[SYSTEM] Starting BingX Data Feed ({FEED_SCRIPT})...{RESET}")
        feed_proc = subprocess.Popen(
            [sys.executable, FEED_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=ENV_VARS,
        )
        processes.append(feed_proc)

        t_feed = threading.Thread(
            target=stream_reader, args=(feed_proc.stdout, "[FEED]", YELLOW)
        )
        t_feed.daemon = True
        t_feed.start()

        # Wait for Feed to bind
        time.sleep(2)

        # 3. Start Bot
        print(f"{BLUE}[SYSTEM] Starting Bot Engine ({BOT_SCRIPT})...{RESET}")
        bot_proc = subprocess.Popen(
            [sys.executable, BOT_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=ENV_VARS,
        )
        processes.append(bot_proc)

        t_bot = threading.Thread(
            target=stream_reader, args=(bot_proc.stdout, "[BOT ]", GREEN)
        )
        t_bot.daemon = True
        t_bot.start()

        print(f"{BLUE}[SYSTEM] Live Trading Running... (Press Ctrl+C to stop){RESET}")

        # Monitor Loop
        while True:
            time.sleep(1)
            # Check if processes died
            if feed_proc.poll() is not None:
                print(f"{RED}❌ Feed Process died unexpectedly.{RESET}")
                break
            if bot_proc.poll() is not None:
                print(f"{RED}❌ Bot Process died unexpectedly.{RESET}")
                break

    except KeyboardInterrupt:
        print(f"\n{YELLOW}[SYSTEM] Stopping Demo...{RESET}")
    finally:
        print(f"{BLUE}[SYSTEM] Terminating subprocesses...{RESET}")
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()

        # Final cleanup
        kill_port(5555)
        kill_port(5557)
        print(f"{BLUE}[SYSTEM] Done.{RESET}")


if __name__ == "__main__":
    main()
