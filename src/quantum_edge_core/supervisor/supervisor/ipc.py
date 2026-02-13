"""
Inter-Process Communication for SupervisorAgent.
Handles broadcasting of Policy Updates to the Grid/Bot.
"""

from __future__ import annotations

import json
import logging
import zmq
import zmq.asyncio
from typing import Dict, Any, Optional
from dataclasses import asdict
from enum import Enum
from quantum_edge_core.supervisor.domain.models import PolicyContract

logger = logging.getLogger(__name__)


class PolicyPublisher:
    """
    Publishes Supervisor policies to the trading bot(s).
    """

    def __init__(self, zmq_context: Optional[zmq.asyncio.Context] = None, pub_port: int = 5556):
        self.ctx = zmq_context or zmq.asyncio.Context()
        self.pub_port = pub_port
        self.socket: Optional[zmq.asyncio.Socket] = None
        self.topic = "system.policy"

    async def start(self):
        """Start the publisher."""
        try:
            self.socket = self.ctx.socket(zmq.PUB)
            self.socket.bind(f"tcp://0.0.0.0:{self.pub_port}")
            logger.info(f"PolicyPublisher bound to tcp://0.0.0.0:{self.pub_port}")
        except zmq.ZMQError as e:
            logger.error(f"Failed to bind ZMQ socket: {e}")
            raise

    async def publish_update(self, policy_data: Dict[str, Any]):
        """Legacy method alias."""
        # Check if it looks like the new contract, if so use new method but we need PolicyContract obj
        # This is for backward compat if any line uses it with dict
        pass

    async def publish_policy(self, policy: PolicyContract):
        """
        Broadcast a policy update.
        """
        if not self.socket:
            logger.warning("PolicyPublisher not started. Skipping broadcast.")
            return

        try:
            # Serialize
            # Custom encoder for Enum
            def default(o):
                if isinstance(o, Enum):
                    return o.value
                return str(o)

            payload = asdict(policy)
            json_str = json.dumps(payload, default=default)

            # Send: [Topic] [JSON]
            # msg = [topic, json_str]
            await self.socket.send_multipart([self.topic.encode("utf-8"), json_str.encode("utf-8")])
            logger.debug(f"Broadcasting Policy: {policy.mode} (Mult: {policy.risk_multiplier})")

        except Exception as e:
            logger.error(f"Failed to publish policy: {e}")

    async def stop(self):
        if self.socket:
            self.socket.close()
            # self.ctx.term() # Usually managed globally
