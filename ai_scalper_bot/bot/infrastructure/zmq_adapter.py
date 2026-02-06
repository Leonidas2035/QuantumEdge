import zmq
import zmq.asyncio
import ujson
import asyncio

class ZmqSubStream:
    def __init__(self, port=5555):
        self.ctx = zmq.asyncio.Context()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.connect(f"tcp://localhost:{port}")
        self.sock.setsockopt_string(zmq.SUBSCRIBE, "")
    
    async def get_latest_tick(self):
        try:
            msg = await self.sock.recv_string()
            return ujson.loads(msg)
        except Exception as e:
            print(f"ZMQ Error: {e}")
            return None
