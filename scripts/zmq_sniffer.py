#!/usr/bin/env python3
"""ZMQ Pipeline Sniffer — Diagnoses which events the Hub broadcasts.

Subscribes to ALL topics on the Hub's ZMQ PUB socket and counts/logs
events by type. This isolates whether the problem is at the
Broadcast (ZMQ) layer or before it.

Usage:
    python scripts/zmq_sniffer.py [--port 5555] [--duration 30]
"""

import argparse
import json
import signal
import sys
import time
from collections import Counter

import zmq


def main():
    parser = argparse.ArgumentParser(description="ZMQ PUB sniffer")
    parser.add_argument("--port", type=int, default=5555,
                        help="ZMQ PUB port (default: 5555)")
    parser.add_argument("--duration", type=int, default=30,
                        help="Seconds to listen (default: 30)")
    parser.add_argument("--topic", type=str, default="",
                        help="Topic filter (default: '' = all)")
    args = parser.parse_args()

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://127.0.0.1:{args.port}")
    sub.setsockopt_string(zmq.SUBSCRIBE, args.topic)
    sub.setsockopt(zmq.RCVTIMEO, 2000)  # 2s timeout per recv

    print(f"🔍 Sniffing ZMQ PUB on tcp://127.0.0.1:{args.port}")
    print(f"   Topic filter: '{args.topic or '<all>'}'")
    print(f"   Duration: {args.duration}s")
    print("=" * 70)

    counts: Counter = Counter()
    samples: dict = {}
    start = time.time()

    def _sigint(*a):
        pass
    signal.signal(signal.SIGINT, _sigint)

    while time.time() - start < args.duration:
        try:
            raw = sub.recv_multipart()
            topic = raw[0].decode("utf-8", errors="replace")
            payload = raw[1].decode("utf-8", errors="replace") if len(raw) > 1 else ""

            counts[topic] += 1

            # Store first sample per topic
            if topic not in samples:
                samples[topic] = payload[:300]
                print(f"\n📦 NEW TOPIC: {topic}")
                print(f"   Sample: {payload[:200]}")

        except zmq.Again:
            continue
        except KeyboardInterrupt:
            break

    elapsed = time.time() - start
    sub.close()
    ctx.term()

    # Summary
    print("\n" + "=" * 70)
    print(f"📊 SUMMARY ({elapsed:.1f}s)")
    print("=" * 70)

    if not counts:
        print("  ⚠️  NO MESSAGES RECEIVED — Hub may not be broadcasting!")
    else:
        for topic, count in counts.most_common():
            rate = count / elapsed if elapsed > 0 else 0
            print(f"  {topic:40s}  {count:6d} msgs  ({rate:.1f}/s)")

    # Diagnosis
    print("\n" + "=" * 70)
    print("🔬 DIAGNOSIS")
    print("=" * 70)

    depth_topics = [t for t in counts if "depth" in t or "orderbook" in t or "book" in t]
    kline_topics = [t for t in counts if "kline" in t]
    wall_topics = [t for t in counts if "wall" in t]

    if kline_topics:
        print(f"  ✅ Kline events detected: {kline_topics}")
    else:
        print("  ❌ NO kline events — Ingest layer broken")

    if depth_topics:
        print(f"  ✅ Depth/OrderBook events detected: {depth_topics}")
    else:
        print("  ❌ NO depth/orderbook events — Check BinanceFuturesFeed._handle_depth()")

    if wall_topics:
        print(f"  ✅ Whale wall events detected: {wall_topics}")
    else:
        print("  ⚠️  No whale wall events — aggregator may not find walls (normal if market is calm)")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
