"""
================================================================
QuantumEdge — Orderbook & Trade Prints Collector (WS)
================================================================
Запускає WebSocket з'єднання з біржею (Bybit або BingX) через ccxt.pro.
Паралельно збирає:
  1. watchOrderBook (знімки стакану глибиною N)
  2. watchTrades (стрічка угод)
Пише зібрані дані в реальному часі у JSONL-файл в runtime/orderflow_raw/.

Безпека ключів: BingX API-ключі беруться ВИКЛЮЧНО з .env (через dotenv),
не хардкодяться. Bybit публічні дані — без ключа.

Приклади:
  .venv/bin/python3 -m tools.orderflow.collector --symbol BTC/USDT:USDT --exchange bybit --duration 21600
  .venv/bin/python3 -m tools.orderflow.collector --symbol BTC/USDT:USDT --exchange bingx --duration 21600
  .venv/bin/python3 -m tools.orderflow.collector --symbol BTC/USDT:USDT --exchange both  --duration 21600
================================================================
"""
from __future__ import annotations
import sys
import json
import asyncio
import argparse
import datetime as dt
from pathlib import Path

import ccxt.pro as ccxtpro
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # .env може бути відсутнім — Bybit працює без нього

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

OUT_DIR = Path("/home/layman/QuantumEdge/runtime/orderflow_raw")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _make_exchange(name: str, symbol: str):
    """Створює ccxt.pro-біржу. BingX — з ключами з .env (якщо є)."""
    opts = {"enableRateLimit": True,
            "options": {"defaultType": "linear" if ":" in symbol else "spot"}}
    if name == "bingx":
        import os
        key = os.environ.get("BINGX_API_KEY") or os.environ.get("BINGX_DEMO_API_KEY")
        secret = os.environ.get("BINGX_API_SECRET") or os.environ.get("BINGX_DEMO_API_SECRET")
        if key and secret:
            opts["apiKey"] = key
            opts["secret"] = secret
            print(f"[bingx] using API key from .env (len={len(key)})")
        else:
            print("[bingx] WARN: ключі BINGX_API_KEY/SECRET не знайдені в .env — публічний WS може бути обмежений")
    return getattr(ccxtpro, name)(opts)


async def collect_book(exchange, symbol, depth, queue):
    """Стрімить знімки стакану та додає їх у чергу запису."""
    while True:
        try:
            book = await exchange.watch_order_book(symbol, limit=depth)
            payload = {
                "type": "book",
                "timestamp": book.get("timestamp") or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
                "bids": [[float(p), float(s)] for p, s in book["bids"][:depth]],
                "asks": [[float(p), float(s)] for p, s in book["asks"][:depth]],
            }
            await queue.put(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def collect_trades(exchange, symbol, queue):
    """Стрімить угоди та додає їх у чергу запису."""
    while True:
        try:
            trades = await exchange.watch_trades(symbol)
            for t in trades:
                payload = {
                    "type": "trade",
                    "timestamp": t.get("timestamp") or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
                    "price": float(t["price"]),
                    "amount": float(t["amount"]),
                    "side": t.get("side"),  # 'buy' або 'sell'
                }
                await queue.put(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def writer_loop(queue, base_path: Path, rotate_minutes: int = 60, max_bytes: int = 2_000_000_000):
    """Пише JSONL на диск з РОТАЦІЙОЮ по часу та лімітом розміру файлу.
    - новий файл кожні rotate_minutes хв (захист від гігантських файлів)
    - якщо файл перевищує max_bytes — примусова ротація
    Це запобігає заповненню диска одним велетнем і спрощує бектест (читання пачкою).
    """
    counter = 1

    def _next_path():
        nonlocal counter
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        path = base_path.parent / f"{base_path.stem}_{counter:03d}_{ts}.jsonl"
        counter += 1
        return path

    cur_path = _next_path()
    f = open(cur_path, "a", encoding="utf-8")
    print(f"[writer] -> {cur_path}")
    rotated_at = dt.datetime.now(dt.timezone.utc)
    size = cur_path.stat().st_size if cur_path.exists() else 0

    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        line = json.dumps(item, ensure_ascii=False) + "\n"
        f.write(line)
        size += len(line.encode("utf-8"))
        now = dt.datetime.now(dt.timezone.utc)
        if (now - rotated_at).total_seconds() >= rotate_minutes * 60 or size >= max_bytes:
            f.close()
            # Друкуємо лог ротації зі старим шляхом та його реальним розміром
            print(f"[writer] rotated -> {cur_path} (size={size/1e6:.1f}MB)")
            cur_path = _next_path()
            f = open(cur_path, "a", encoding="utf-8")
            rotated_at = now
            size = 0
        f.flush()
        queue.task_done()


async def run_one(exchange_name, symbol, depth, duration, queue, tag=""):
    """Запускає збір для однієї біржі: book + trades таски."""
    ex = _make_exchange(exchange_name, symbol)
    tasks = [
        asyncio.create_task(collect_book(ex, symbol, depth, queue)),
        asyncio.create_task(collect_trades(ex, symbol, queue)),
    ]
    try:
        if duration > 0:
            await asyncio.sleep(duration)
        else:
            while True:
                await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await ex.close()
        print(f"[{exchange_name}] collector stopped.")


async def main_async(args):
    symbol = args.symbol
    depth = args.depth
    duration = args.duration
    exchanges = args.exchange.split(",")  # bybit / bingx / both

    clean_sym = symbol.replace("/", "_").replace(":", "_")
    base_name = f"orderflow_{clean_sym}"  # ротація додасть _HHMM
    base_path = Path(args.out_dir) / base_name
    rotate_min = args.rotate_minutes

    print(f"Starting collector for {symbol} (depth={depth}, exchanges={exchanges}, "
          f"rotate={rotate_min}m). Base -> {base_path}_HHMM.jsonl")

    queue = asyncio.Queue()
    writer = asyncio.create_task(writer_loop(queue, base_path, rotate_min, args.max_bytes))

    runners = [asyncio.create_task(run_one(x.strip(), symbol, depth, duration, queue)) for x in exchanges]

    try:
        await asyncio.gather(*runners)
    finally:
        await queue.put(None)
        await writer
        print("Collector stopped successfully.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT:USDT", help="торгова пара")
    ap.add_argument("--depth", type=int, default=50, help="глибина стакану")
    ap.add_argument("--duration", type=int, default=0, help="тривалість збору в сек (0 = нескінченно)")
    ap.add_argument("--exchange", default="bybit", help="bybit | bingx | both (через кому)")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="папка запису")
    ap.add_argument("--rotate-minutes", type=int, default=60, help="ротація файлу кожні N хв (захист диска)")
    ap.add_argument("--max-bytes", type=int, default=2_000_000_000, help="примусова ротація при перевищенні розміру (за замовч 2GB)")
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")


if __name__ == "__main__":
    main()
