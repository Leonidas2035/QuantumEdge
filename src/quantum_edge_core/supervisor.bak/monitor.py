"""
Monitor module for SupervisorAgent.
Handles ZMQ subscriptions and monitoring tasks.
"""

import zmq
import json
import logging
from typing import Optional, Dict, Any


class ZmqHeartbeatSubscriber:
    """Synchronous ZMQ Subscriber for Heartbeats."""

    def __init__(
        self, endpoint: str = "tcp://127.0.0.1:5557", expected_id: Optional[str] = None
    ):
        self.ctx = zmq.Context()
        self.expected_id = expected_id
        self.socket = self.ctx.socket(zmq.SUB)
        self.socket.setsockopt(zmq.RCVTIMEO, 2000)
        self.socket.setsockopt(zmq.SNDTIMEO, 2000)
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


class ZmqCommandPublisher:
    """Synchronous ZMQ Publisher for sending commands to bots."""

    def __init__(self, endpoint: str = "tcp://*:5558"):
        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.PUB)
        # Prevent blocking and endless queues
        self.socket.setsockopt(zmq.SNDTIMEO, 2000)
        self.socket.setsockopt(zmq.LINGER, 0)
        try:
            self.socket.bind(endpoint)
            logging.getLogger(__name__).info(f"Command PUB bound to {endpoint}")
        except zmq.ZMQError as e:
            logging.getLogger(__name__).error(f"Failed to bind Command PUB: {e}")

    def send_command(self, bot_id: str, action: str, **kwargs) -> bool:
        """
        Sends a command to a specific bot.
        bot_id dictates the topic, e.g. "command.ai_scalper_bot"
        """
        import time

        topic = f"command.{bot_id}".encode("utf-8")
        payload = {"action": action, "timestamp": time.time()}
        payload.update(kwargs)

        try:
            msg = json.dumps(payload).encode("utf-8")
            self.socket.send_multipart([topic, msg])
            logging.getLogger(__name__).info(
                f"📤 Command sent to {bot_id}: {action} payload={kwargs}"
            )
            return True
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"Failed to send command to {bot_id}: {e}"
            )
            return False

    def close(self):
        self.socket.close()
        self.ctx.term()
