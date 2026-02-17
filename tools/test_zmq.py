#!/usr/bin/env python3
import zmq
import json
import time


def main():
    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.connect("tcp://127.0.0.1:5555")
    socket.setsockopt_string(zmq.SUBSCRIBE, "")

    print("Listening on tcp://127.0.0.1:5555...")

    try:
        while True:
            # Receive [topic, payload]
            frames = socket.recv_multipart()
            if len(frames) >= 2:
                topic = frames[0].decode()
                payload = frames[1]  # bytes, JSON
                try:
                    data = json.loads(payload)
                    print(f"[{topic}] {data}")
                except json.JSONDecodeError:
                    print(f"[{topic}] (Raw) {payload}")
            else:
                print(f"(Raw) {frames}")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        socket.close()
        ctx.term()


if __name__ == "__main__":
    main()
