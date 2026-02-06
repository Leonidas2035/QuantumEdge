import asyncio
import zmq
import zmq.asyncio
import ujson
import random
import time

# --- CONFIG ---
MOCK_MARKET_PUB_PORT = 5555  # Порт, де бот слухає дані
BOT_REPORT_SUB_PORT = 5557   # Порт, куди бот шле звіти
SYMBOL = "BTCUSDT"

async def mock_market_feed(ctx):
    """Емулює MarketDataHub: шле тіки раз на 10мс"""
    pub = ctx.socket(zmq.PUB)
    pub.bind(f"tcp://*:{MOCK_MARKET_PUB_PORT}")
    print(f"[MOCK MARKET] Publishing on port {MOCK_MARKET_PUB_PORT}...")
    
    price = 50000.0
    
    while True:
        try:
            # Емуляція Random Walk
            change = random.uniform(-5, 5)
            price += change
            
            tick = {
                "s": SYMBOL,
                "p": str(round(price, 2)),
                "q": str(round(random.uniform(0.001, 1.0), 5)),
                "t": int(time.time() * 1000),
                "m": random.choice([True, False]), # Maker/Taker
                # BBO Fields for OFI
                "b": str(round(price - 0.01, 2)),
                "a": str(round(price + 0.01, 2)),
                "B": str(round(random.uniform(1.0, 10.0), 2)),
                "A": str(round(random.uniform(1.0, 10.0), 2))
            }
            
            # Відправка JSON
            await pub.send_string(ujson.dumps(tick))
            await asyncio.sleep(0.01) # 10ms delay
        except Exception as e:
            print(f"Market Feed Error: {e}")
            await asyncio.sleep(1)

async def supervisor_listener(ctx):
    """Емулює Supervisor: слухає, чи живий бот"""
    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://localhost:{BOT_REPORT_SUB_PORT}")
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    print(f"[MOCK SUPERVISOR] Listening on port {BOT_REPORT_SUB_PORT}...")

    while True:
        try:
            msg = await sub.recv_string()
            data = ujson.loads(msg)
            print(f"✅ [HEARTBEAT] State: {data.get('state')} | PnL: {data.get('pnl')}")
        except Exception as e:
            print(f"❌ Error receiving heartbeat: {e}")
            await asyncio.sleep(0.1)

async def main():
    ctx = zmq.asyncio.Context()
    
    print(">>> ЗАПУСК ІЗОЛЬОВАНОГО ТЕСТУ <<<")
    print("1. Запускаю Mock Market Feed...")
    print("2. Запускаю Mock Supervisor Listener...")
    print("3. ТЕПЕР ЗАПУСТИ БОТА В ІНШОМУ ТЕРМІНАЛІ!")
    print("-" * 30)

    await asyncio.gather(
        mock_market_feed(ctx),
        supervisor_listener(ctx)
    )

if __name__ == "__main__":
    asyncio.run(main())
