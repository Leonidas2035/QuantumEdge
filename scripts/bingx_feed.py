import aiohttp
import asyncio
import zmq
import zmq.asyncio
import ujson
import gzip
import io

WS_URL = "wss://open-api-swap.bingx.com/swap-market"
ZMQ_PORT = 5555
SYMBOL = "BTC-USDT"


async def bingx_publisher():
    ctx = zmq.asyncio.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(f"tcp://*:{ZMQ_PORT}")
    print(f">>> [REAL DATA] Connecting to BingX Stream for {SYMBOL}...")

    while True:
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(WS_URL) as ws,
            ):
                sub_msg = {"id": "id1", "reqType": "sub", "dataType": f"trade.{SYMBOL}"}
                await ws.send_str(ujson.dumps(sub_msg))

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        with gzip.GzipFile(fileobj=io.BytesIO(msg.data)) as f:
                            data = f.read()
                        data_str = data.decode("utf-8")
                        if "Ping" in data_str:
                            await ws.send_str("Pong")
                            continue

                        payload = ujson.loads(data_str)
                        if "data" in payload and isinstance(payload["data"], list):
                            for trade in payload["data"]:
                                tick = {
                                    "s": SYMBOL,
                                    "p": str(trade["p"]),
                                    "q": str(trade["q"]),
                                    "t": trade["T"],
                                    "m": trade["m"],
                                }
                                await pub.send_string(ujson.dumps(tick))
        except Exception as e:
            print(f"Reconnect: {e}")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(bingx_publisher())
