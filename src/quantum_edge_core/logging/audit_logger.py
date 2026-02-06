"""
Structured Audit Logger for SupervisorAgent.
Records AI decisions and critical events to a JSONL file.
"""

from __future__ import annotations

import json
import logging
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
import threading
from dataclasses import asdict, is_dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class AuditLogger:
    """
    Logs structured events to a persistent JSONL file.
    Thread-safe.
    """
    def __init__(self, log_dir: str = "data/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "events.jsonl"
        self._lock = threading.Lock()
        
    def _write_entry(self, entry: Dict[str, Any]):
        """Write a dictionary as a JSON line."""
        try:
            # Add timestamp if missing
            if "ts" not in entry:
                entry["ts"] = datetime.now(timezone.utc).isoformat()
            
            json_str = json.dumps(entry)
            
            with self._lock:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json_str + "\n")
                    
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def log_ai_event(self, context: Dict[str, Any], decision: Any, latency_ms: float):
        """
        Log an AI decision event.
        decision: Can be PolicyContract (dataclass) or dict.
        """
        try:
             # Convert dataclass to dict if needed
            if is_dataclass(decision):
                 output_data = asdict(decision)
                 # Handle Enum
                 output_data["mode"] = output_data["mode"].value if hasattr(output_data["mode"], "value") else str(output_data["mode"])
            else:
                 output_data = decision

            entry = {
                "type": "AI_DECISION",
                "latency_ms": latency_ms,
                "input": context, 
                "output": output_data
            }
            self._write_entry(entry)
        except Exception as e:
            logger.error(f"Failed to log AI event: {e}")

    def log_kill_event(self, reason: str, triggering_metric: str, limit_val: float):
        """
        Log an emergency kill switch event.
        """
        entry = {
            "type": "KILL_SWITCH",
            "reason": reason,
            "trigger": triggering_metric,
            "limit": limit_val
        }
        self._write_entry(entry)

    def tail(self, n: int = 10) -> list:
        """
        Read the last N lines.
        """
        if not self.log_file.exists():
            return []
            
        # Simplified tail - reads all and slices. 
        # For huge files, use seek from end.
        try:
            with self._lock:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            
            return [json.loads(line) for line in lines[-n:]]
        except Exception as e:
            logger.error(f"Failed to read tail: {e}")
            return []
