import zmq
import zmq.asyncio
import ujson
import asyncio
import time

class SupervisorReporter:
    def __init__(self, port=5557):
        self.ctx = zmq.asyncio.Context()
        self.sock = self.ctx.socket(zmq.PUB)
        self.sock.bind(f"tcp://*:{port}")
    
    async def send_heartbeat(self, state, pnl):
        msg = {
            "type": "heartbeat",
            "ts": time.time(),
            "state": str(state),
            "pnl": pnl
        }
        await self.sock.send_string(ujson.dumps(msg))
