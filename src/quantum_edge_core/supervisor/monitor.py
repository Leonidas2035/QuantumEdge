"""
Monitor module for SupervisorAgent.
Handles ZMQ subscriptions and monitoring tasks.
"""

import json
import logging
from typing import Any, Dict, Optional

import zmq


class ZmqHeartbeatSubscriber:
    """Synchronous ZMQ Subscriber for Heartbeats."""

    def __init__(
        self, endpoint: str = "tcp://127.0.0.1:5557", expected_id: Optional[str] = None
    ):
        self.ctx = zmq.Context()
        self.expected_id = expected_id
        self.socket = self.ctx.socket(zmq.SUB)
        self.socket.setsockopt(zmq.LINGER, 0)
        # Prevent blocking on close
        self.socket.setsockopt(zmq.RCVTIMEO, 0)
        try:
            self.socket.connect(endpoint)
            self.socket.subscribe(b"telemetry")
        except zmq.ZMQError:
            pass  # Log or handle?

    def check_messages(self) -> Optional[Dict[str, Any]]:
        try:
            # Non-blocking poll
            if self.socket.poll(0):
                topic, msg = self.socket.recv_multipart()
                payload = json.loads(msg.decode("utf-8"))

                # Identity check
                if self.expected_id:
                    source = payload.get("source") or payload.get("service_id")
                    if source != self.expected_id:
                        logging.getLogger(__name__).warning(
                            f"Ignored heartbeat from unknown source: {source}, expected: {self.expected_id}"
                        )
                        return None
                return payload
        except (zmq.ZMQError, ValueError, json.JSONDecodeError):
            pass
        return None

    def close(self):
        self.socket.close()
        self.ctx.term()
