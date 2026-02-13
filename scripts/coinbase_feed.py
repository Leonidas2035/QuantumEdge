import aiohttp
import asyncio
import zmq
import zmq.asyncio
import ujson

# COINBASE WS (Працює в США)
WS_URL = "wss://ws-feed.exchange.coinbase.com"
ZMQ_PORT = 5555
# Ми транслюємо дані як "BTC-USDT", щоб бот думав, що це BingX
TARGET_SYMBOL = "BTC-USDT"


async def coinbase_publisher():
    ctx = zmq.asyncio.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(f"tcp://*:{ZMQ_PORT}")
    print(">>> [COINBASE FEED] Connecting from US Server...")

    while True:
        try:
            async with aiohttp.ClientSession() as session, session.ws_connect(WS_URL) as ws:
                # Підписка на тікер
                sub_msg = {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}
                await ws.send_str(ujson.dumps(sub_msg))
                print(">>> [DATA FLOW] Connected! Streaming BTC prices to Bot...")

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = ujson.loads(msg.data)

                        if data.get("type") == "ticker":
                            # Нормалізація під формат нашого Бота
                            tick = {
                                "s": TARGET_SYMBOL,  # Підміняємо на BTC-USDT
                                "p": str(data["price"]),
                                "q": str(data["last_size"]),
                                "t": int(asyncio.get_event_loop().time() * 1000),  # Timestamp
                                "m": data.get("side") == "sell",  # Maker logic approx
                            }
                            await pub.send_string(ujson.dumps(tick))

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
        except Exception as e:
            print(f"Reconnect Error: {e}")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(coinbase_publisher())
