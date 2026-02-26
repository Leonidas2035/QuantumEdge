import asyncio
import json
import time
import logging
import zmq
import zmq.asyncio

logger = logging.getLogger(__name__)


class TelemetryPublisher:
    def __init__(self, config: dict):
        self.config = config
        self.port = self.config.get("zmq", {}).get("telemetry", 5557)
        self.ctx = zmq.asyncio.Context.instance()
        self.socket = self.ctx.socket(zmq.PUB)
        # We use bind for PUB if it's a server, but in our architecture,
        # Supervisor binds 5557. The Bot must CONNECT to it.
        self.socket.connect(f"tcp://127.0.0.1:{self.port}")
        self._running = False

    async def start(self):
        self._running = True
        logger.info(f"TelemetryPublisher connected to port {self.port}")
        while self._running:
            payload = {
                "source": "ai_scalper_bot",
                "type": "heartbeat",
                "ts": int(time.time()),
                "state": "RUNNING",
            }
            message = [b"telemetry", json.dumps(payload).encode("utf-8")]
            await self.socket.send_multipart(message)
            await asyncio.sleep(3.0)

    def stop(self):
        self._running = False
        self.socket.close()
