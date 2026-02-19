import datetime

import zmq


def listen():
    context = zmq.Context()
    socket = context.socket(zmq.SUB)

    # Standard MarketDataHub PUB port
    PORT = 5555

    print(f"🔌 Connecting to ZMQ Subscriber on port {PORT}...")
    socket.connect(f"tcp://127.0.0.1:{PORT}")
    socket.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to ALL topics

    print("👂 Waiting for market data tick...")

    while True:
        try:
            # Receive message
            msg = socket.recv_string()
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")
            # Try to print a preview
            print(f"[{timestamp}] ✅ RECEIVED: {msg[:100]}...")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    listen()
