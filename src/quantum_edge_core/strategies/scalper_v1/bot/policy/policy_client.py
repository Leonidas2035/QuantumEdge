"""
Policy Client for Bot.
Subscribes to Supervisor's 'system.policy' via ZMQ.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zmq
import zmq.asyncio
from typing import Dict, Any
from dataclasses import dataclass

# Attempt import from domain, else define local fallback or fail
try:
    from quantum_edge_core.supervisor.domain.models import PolicyContract, TradingMode
except ImportError:
    # Fallback definition if import fails (e.g. strict isolation)
    # Ideally should share code. For now assuming import works as they are in same repo/package structure.
    # If not, we would redefine here.
    logging.warning(
        "Could not import PolicyContract from supervisor.domain. Using local definition."
    )
    from enum import Enum

    class TradingMode(Enum):
        NORMAL = "normal"
        CONSERVATIVE = "conservative"
        SNIPER = "sniper"
        WINTER = "winter"
        FREEZE = "freeze"
        REDUCE_ONLY = "reduce_only"
        HALT = "halt"

    @dataclass
    class PolicyContract:
        mode: TradingMode
        long_allowed: bool
        short_allowed: bool
        max_leverage: float
        min_order_size: float
        max_position_size: float
        risk_multiplier: float = 1.0
        ai_confidence: float = 0.0
        ai_reasoning: str = ""
        close_only: bool = False


logger = logging.getLogger(__name__)


class PolicyClient:
    """
    Async ZMQ Subscriber for System Policy.
    """

    def __init__(self, zmq_url: str = "tcp://127.0.0.1:5556"):
        self.zmq_url = zmq_url
        self.ctx = zmq.asyncio.Context()
        self.socket = None

        # Default / Safe Fallback
        self._current_policy = PolicyContract(
            mode=TradingMode.CONSERVATIVE,
            long_allowed=True,
            short_allowed=True,
            max_leverage=5.0,  # Reduced
            min_order_size=10.0,
            max_position_size=1000.0,
            risk_multiplier=0.5,
            ai_reasoning="Initializing...",
            close_only=False,
        )
        self._last_update_ts = 0.0
        self.running = False
        self._task = None

    async def start(self):
        """Start the background subscription task."""
        self.running = True
        self.socket = self.ctx.socket(zmq.SUB)
        self.socket.connect(self.zmq_url)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "system.policy")

        self._task = asyncio.create_task(self._subscribe_loop())
        logger.info(f"PolicyClient connected to {self.zmq_url}")

    async def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _subscribe_loop(self):
        """Background loop to receive updates."""
        while self.running:
            try:
                # Receive multipart: [topic, json_payload]
                topic, msg = await self.socket.recv_multipart()
                data = json.loads(msg.decode("utf-8"))

                self._update_local_policy(data)
                self._last_update_ts = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Policy sub loop: {e}")
                await asyncio.sleep(1.0)  # Backoff

    def _update_local_policy(self, data: Dict[str, Any]):
        """Parse dict back to PolicyContract."""
        try:
            # Handle Enum conversion
            mode_str = data.get("mode", "normal")
            try:
                mode = TradingMode(mode_str)
            except ValueError:
                mode = TradingMode.NORMAL

            self._current_policy = PolicyContract(
                mode=mode,
                long_allowed=bool(data.get("long_allowed", True)),
                short_allowed=bool(data.get("short_allowed", True)),
                max_leverage=float(data.get("max_leverage", 10.0)),
                min_order_size=float(data.get("min_order_size", 10.0)),
                max_position_size=float(data.get("max_position_size", 1000.0)),
                risk_multiplier=float(data.get("risk_multiplier", 1.0)),
                ai_confidence=float(data.get("ai_meta", {}).get("confidence", 0.0)),
                ai_reasoning=str(data.get("ai_meta", {}).get("reasoning", "")),
                close_only=bool(data.get("close_only", False)),
            )
        except Exception as e:
            logger.error(f"Failed to parse policy update: {e}")

    def get_current_policy(self) -> PolicyContract:
        """
        Return current policy.
        Enforce TTL Fallback if stale.
        """
        # TTL Check (e.g., 30s silent)
        if time.time() - self._last_update_ts > 30.0:
            if self._current_policy.mode != TradingMode.CONSERVATIVE:
                logger.warning("Policy Stale (>30s). Reverting to FALLBACK/Safe Mode.")
                # Return a safe fallback constructed on the fly or modify current
                # Returning a safe copy
                return PolicyContract(
                    mode=TradingMode.CONSERVATIVE,
                    long_allowed=True,
                    short_allowed=True,
                    max_leverage=5.0,
                    min_order_size=10.0,
                    max_position_size=1000.0,
                    risk_multiplier=0.5,
                    ai_reasoning="STALE_FALLBACK",
                    close_only=False,  # Or true?
                )

        return self._current_policy
