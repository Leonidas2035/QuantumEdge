"""
Thread-Safe State Manager for Supervisor.
Protects shared state between Asyncio Main Loop (Writer) and API Thread (Reader).
"""

import threading
import copy
import time
from typing import Dict, Any

class ThreadSafeStateManager:
    """
    Manages atomic updates and reads of the system state.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "startup_timestamp": time.time(),
            "status": "INITIALIZING",
            "regime": "UNKNOWN",
            "active_policy": {},
            "last_heartbeat_time": 0.0,
            "emergency_mode": False,
            "heartbeat_age_s": 0.0
        }

    def update(self, updates: Dict[str, Any]):
        """
        Atomic update of specific keys.
        Called by the Supervisor (Writer).
        """
        with self._lock:
            self._state.update(updates)

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Atomic read returning a deep copy.
        Called by the Dashboard API (Reader).
        """
        with self._lock:
            return copy.deepcopy(self._state)
