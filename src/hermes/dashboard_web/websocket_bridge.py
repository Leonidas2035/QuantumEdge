import asyncio
import json
import logging
import zmq
import zmq.asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WebSocketBridge")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Failed to send to client: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

async def zmq_listener():
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect("tcp://127.0.0.1:5555")
    sock.setsockopt_string(zmq.SUBSCRIBE, "HUB:BTCUSDT:orderbook")
    sock.setsockopt_string(zmq.SUBSCRIBE, "HUB:BTCUSDT:ticker")
    sock.setsockopt_string(zmq.SUBSCRIBE, "HUB:BTCUSDT:trade")
    
    logger.info("ZMQ Listener connected to tcp://127.0.0.1:5555")
    
    while True:
        try:
            topic, payload = await sock.recv_multipart()
            topic_str = topic.decode("utf-8")
            data = json.loads(payload.decode("utf-8"))
            
            message = json.dumps({
                "type": topic_str,
                "data": data
            })
            await manager.broadcast(message)
        except Exception as e:
            logger.error(f"ZMQ Listener error: {e}")
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(zmq_listener())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("websocket_bridge:app", host="0.0.0.0", port=8000, reload=True)
