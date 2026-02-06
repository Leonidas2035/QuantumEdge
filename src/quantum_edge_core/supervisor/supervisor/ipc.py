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

logger = logging.getLogger(__name__)

class PolicyPublisher:
    """
    Publishes Supervisor policies to the trading bot(s).
    """
    def __init__(self, zmq_context: Optional[zmq.asyncio.Context] = None, pub_port: int = 5556):
        self.ctx = zmq_context or zmq.asyncio.Context()
        self.pub_port = pub_port
        self.socket: Optional[zmq.asyncio.Socket] = None
        self.topic = "system.policy_update"

    async def start(self):
        """Start the publisher."""
        self.socket = self.ctx.socket(zmq.PUB)
        self.socket.bind(f"tcp://0.0.0.0:{self.pub_port}")
        logger.info(f"PolicyPublisher bound to tcp://0.0.0.0:{self.pub_port}")

    async def publish_update(self, policy: Dict[str, Any]):
        """
        Broadcast a policy update.
        """
        if not self.socket:
            logger.warning("PolicyPublisher not started. Skipping broadcast.")
            return

        try:
            # Structure the message
            message = {
                "version": 1,
                "type": "policy_update",
                "payload": policy
            }
            json_str = json.dumps(message)
            
            # Send: [Topic] [JSON]
            await self.socket.send_multipart([
                self.topic.encode("utf-8"),
                json_str.encode("utf-8")
            ])
            logger.info(f"Broadcasting Policy: {policy.get('action')} ({policy.get('regime')})")
            
        except Exception as e:
            logger.error(f"Failed to publish policy: {e}")

    async def stop(self):
        if self.socket:
            self.socket.close()
