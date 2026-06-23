"""
Dashboard API for SupervisorAgent.
Provides real-time visibility into the system state and audit logs.
"""

from __future__ import annotations

import logging
import threading
import uvicorn
from typing import Optional
from fastapi import FastAPI

# We will need access to Supervisor state.
# Simplest pattern: Global singleton or Dependency Injection logic usually.
# For this refactor, we'll use a shared state container pattern.

logger = logging.getLogger(__name__)

from hermes.state_manager import ThreadSafeStateManager

logger = logging.getLogger(__name__)

# Shared Global State (managed via ThreadSafeStateManager)
_STATE_MANAGER_REF: Optional[ThreadSafeStateManager] = None


def set_state_manager(manager: ThreadSafeStateManager):
    global _STATE_MANAGER_REF
    _STATE_MANAGER_REF = manager


app = FastAPI(title="QuantumEdge Supervisor API")


@app.get("/status")
async def get_status():
    if not _STATE_MANAGER_REF:
        return {"status": "INITIALIZING"}

    return _STATE_MANAGER_REF.get_snapshot()


@app.get("/audit/tail")
async def get_audit_tail(n: int = 10):
    # AuditLogger is separate, usually safe-ish to read file,
    # but strictly speaking we might want access via manager or kept separate.
    # The prompt mainly concerned /status race conditions.
    # We will assume we can still access the logger if passed or keep it simple.
    # For now, let's assume we can't access supervisor instance easily if we removed the ref.
    # But usually Logger reads from disk, so it doesn't need the Supervisor instance memory.
    # We need to find the log file path.
    from quantum_edge_core.logging.audit_logger import AuditLogger

    # Create a temp logger just to read tail? Or better, pass logger to API?
    # The prompt didn't strictly say to refactor this part, but we lost _SUPERVISOR_REF.
    # Let's instantiate a temporary reader or assume logs are at default location.
    temp_logger = AuditLogger()
    return temp_logger.tail(n)


class DashboardServer(threading.Thread):
    def __init__(
        self, state_manager: ThreadSafeStateManager, host="0.0.0.0", port=8000
    ):
        super().__init__()
        self.host = host
        self.port = port
        self.daemon = True
        set_state_manager(state_manager)

    def run(self):
        logger.info(f"Starting API Server on {self.host}:{self.port}")
        # Disable uvicorn standard logging to avoid noise if desired,
        # or keep it. loop="none" is critical if main thread has loop.
        # But uvicorn.run sets up a loop. Since we are in a Thread,
        # it can have its own loop.
        uvicorn.run(app, host=self.host, port=self.port, loop="asyncio")
