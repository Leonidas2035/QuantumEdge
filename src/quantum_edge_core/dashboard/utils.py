"""QuantumEdge Dashboard Utilities."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import psutil
import psycopg2
import zmq
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --- Database Setup ---


def get_db_connection() -> Optional[psycopg2.extensions.connection]:
    """Get a connection to QuestDB, return None if unavailable. Read secrets from .env"""
    try:
        conn = psycopg2.connect(
            host=os.getenv("QUESTDB_HOST", "127.0.0.1"),
            port=int(os.getenv("QUESTDB_PORT", "8812")),
            dbname=os.getenv("QUESTDB_NAME", "qdb"),
            user=os.getenv("QUESTDB_USER", "admin"),
            password=os.getenv("QUESTDB_PASSWORD", "quest"),
        )
        return conn
    except Exception as e:
        logger.warning(f"Failed to connect to QuestDB: {e}")
        return None


def fetch_data(query: str, fallback_func) -> tuple[pd.DataFrame, bool]:
    """Fetch data from QuestDB or use fallback function.
    Returns: (DataFrame, is_mock)
    """
    conn = get_db_connection()
    if conn:
        try:
            df = pd.read_sql_query(query, conn)
            conn.close()
            if df.empty:
                return fallback_func(), True
            return df, False
        except Exception as e:
            logger.warning(f"Query failed: {e}. Using fallback.")
            if conn:
                conn.close()
            return fallback_func(), True
    else:
        return fallback_func(), True


# --- Mock Data Generators ---


def get_mock_market_data() -> pd.DataFrame:
    """Return empty dataframe instead of mock market data."""
    logger.warning("QuestDB empty after wipe. Showing BLANK table (no mock data!)")
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def get_mock_llm_advice() -> pd.DataFrame:
    """Return empty dataframe instead of mock LLM advice."""
    logger.warning("QuestDB empty after wipe. Showing BLANK table (no mock data!)")
    return pd.DataFrame(columns=["time", "trading_mode", "multiplier", "reason"])


def get_mock_trades() -> pd.DataFrame:
    """Return empty dataframe instead of mock trades data."""
    logger.warning("QuestDB empty after wipe. Showing BLANK table (no mock data!)")
    return pd.DataFrame(
        columns=["timestamp", "symbol", "side", "price", "qty", "realized_pnl"]
    )


def get_mock_inventory() -> pd.DataFrame:
    """Return empty dataframe instead of mock inventory data."""
    logger.warning("QuestDB empty after wipe. Showing BLANK table (no mock data!)")
    return pd.DataFrame(columns=["timestamp", "equity", "drawdown"])


def get_mock_orderbook() -> pd.DataFrame:
    """Return empty dataframe instead of mock orderbook data."""
    logger.warning("QuestDB empty after wipe. Showing BLANK table (no mock data!)")
    return pd.DataFrame(columns=["timestamp", "price", "volume", "side"])


# --- Process Management ---

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"

# Ensure runtime dir exists
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


class ProcessManager:
    @staticmethod
    def get_pid_file(name: str) -> Path:
        return RUNTIME_DIR / f"{name}.pid"

    @staticmethod
    def is_running(name: str) -> bool:
        pid_file = ProcessManager.get_pid_file(name)
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text().strip())
            return psutil.pid_exists(pid)
        except ValueError:
            return False

    @staticmethod
    def get_pid(name: str) -> Optional[int]:
        pid_file = ProcessManager.get_pid_file(name)
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text().strip())
        except ValueError:
            return None

    @staticmethod
    def start_process(name: str, cmd: str) -> bool:
        if ProcessManager.is_running(name):
            return True

        try:
            # Output goes to {name}.log in PROJECT_ROOT as requested
            log_path = PROJECT_ROOT / f"{name.lower()}.log"
            with open(log_path, "a") as out:
                proc = subprocess.Popen(
                    cmd.split(),
                    cwd=str(PROJECT_ROOT),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
            ProcessManager.get_pid_file(name).write_text(str(proc.pid))
            return True
        except Exception as e:
            logger.error(f"Failed to start {name}: {e}")
            return False

    @staticmethod
    def stop_process(name: str) -> bool:
        pid = ProcessManager.get_pid(name)
        if not pid or not psutil.pid_exists(pid):
            return True

        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
            ProcessManager.get_pid_file(name).unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to stop {name}: {e}")
            return False

    @staticmethod
    def restart_process(name: str, cmd: str) -> bool:
        ProcessManager.stop_process(name)
        time.sleep(1)
        return ProcessManager.start_process(name, cmd)

    @staticmethod
    def cold_start_full_system() -> bool:
        try:
            script_path = PROJECT_ROOT / "scripts" / "cold_start_debug.sh"
            subprocess.Popen([str(script_path)], cwd=str(PROJECT_ROOT), shell=False)
            return True
        except Exception as e:
            logger.error(f"Cold start failed: {e}")
            return False


# --- ZMQ Control ---


def send_halt_command():
    """Send HALT command to port 5558."""
    try:
        context = zmq.Context.instance()
        socket = context.socket(zmq.PUB)
        socket.connect("tcp://127.0.0.1:5558")
        time.sleep(0.1)  # wait for connection
        msg = json.dumps(
            {"action": "HALT", "source": "dashboard", "timestamp": time.time()}
        )
        socket.send_multipart([b"CONTROL", msg.encode("utf-8")])
        socket.close()
        return True
    except Exception as e:
        logger.error(f"Failed to send HALT: {e}")
        return False


def force_apply_mode(mode: str):
    """Force apply a trading mode via ZMQ."""
    try:
        context = zmq.Context.instance()
        socket = context.socket(zmq.PUB)
        socket.connect("tcp://127.0.0.1:5558")
        time.sleep(0.1)
        msg = json.dumps(
            {"trading_mode": mode, "source": "dashboard", "timestamp": time.time()}
        )
        socket.send_multipart([b"CONTROL", msg.encode("utf-8")])
        socket.close()
        return True
    except Exception as e:
        logger.error(f"Failed to force mode {mode}: {e}")
        return False


# --- Log Tailing ---


from collections import deque


def tail_log(filename: str, lines: int = 50) -> str:
    """Tail a log file from PROJECT_ROOT memory-efficiently."""
    filepath = PROJECT_ROOT / filename
    if not filepath.exists():
        return f"File not found: {filepath}"

    try:
        with open(filepath, "r") as f:
            return "".join(deque(f, maxlen=lines))
    except Exception as e:
        return f"Error reading log: {e}"


def clear_logs():
    """Clear the log files."""
    logs = ["hub.log", "supervisor.log", "bot.log"]
    for log in logs:
        path = PROJECT_ROOT / log
        if path.exists():
            path.write_text("")
