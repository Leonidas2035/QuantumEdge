#!/usr/bin/env python3
"""Diagnostic: Test Binance WebSocket streams (Testnet @trade vs Mainnet @kline_1m)."""

import asyncio
import json
import time
import websockets


async def test_stream(label: str, url: str, max_msgs: int = 2, timeout: float = 15.0):
    """Try to receive max_msgs from a WS endpoint within timeout seconds."""
    print(f"\n{'='*60}")
    print(f"[{label}] Connecting to: {url}")
    print(f"{'='*60}")
    try:
        async with websockets.connect(url, close_timeout=5) as ws:
            print(f"[{label}] ✅ Connected")
            received = 0
            t0 = time.time()
            while received < max_msgs:
                remaining = timeout - (time.time() - t0)
                if remaining <= 0:
                    print(f"[{label}] ⏰ Timeout after {timeout}s — only got {received}/{max_msgs} messages")
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    received += 1
                    data = json.loads(msg)
                    print(f"[{label}] 📩 Message #{received}: {json.dumps(data)[:200]}")
                except asyncio.TimeoutError:
                    print(f"[{label}] ⏰ Timeout — no data for {timeout}s")
                    break
            elapsed = time.time() - t0
            if received >= max_msgs:
                print(f"[{label}] ✅ SUCCESS: received {received} messages in {elapsed:.1f}s")
            else:
                print(f"[{label}] ❌ FAILED: only {received}/{max_msgs} messages in {elapsed:.1f}s")
    except Exception as exc:
        print(f"[{label}] ❌ Connection failed: {exc}")


async def main():
    print("=" * 60)
    print("QuantumEdge WS Stream Diagnostic")
    print("=" * 60)

    # Test 1: Testnet @trade (suspected dead)
    await test_stream(
        "TESTNET @trade",
        "wss://stream.binancefuture.com/ws/btcusdt@trade",
        max_msgs=2,
        timeout=15.0,
    )

    # Test 2: Mainnet @kline_1m (should be active)
    await test_stream(
        "MAINNET @kline_1m",
        "wss://fstream.binance.com/ws/btcusdt@kline_1m",
        max_msgs=2,
        timeout=15.0,
    )

    # Test 3: Mainnet @trade (for comparison)
    await test_stream(
        "MAINNET @trade",
        "wss://fstream.binance.com/ws/btcusdt@trade",
        max_msgs=2,
        timeout=10.0,
    )

    print("\n" + "=" * 60)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
