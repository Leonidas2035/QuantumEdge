import time

from quantum_edge_core.events import BaseEvent, EventCodec, MarketTrade


def main():
    print("Testing Event Serialization...")

    # 1. Create a MarketTrade event
    original_event = MarketTrade(
        symbol="BTCUSDT",
        price=100000.0,
        quantity=0.1,
        side="buy",
        timestamp=time.time(),
    )
    print(f"Original: {original_event}")

    # 2. Encode to bytes
    encoded = EventCodec.encode(original_event)
    print(f"Encoded (bytes): {encoded}")
    print(f"Encoded (str): {encoded.decode('utf-8')}")

    # 3. Decode back to object
    decoded = EventCodec.decode(encoded)
    print(f"Decoded: {decoded}")

    # 4. Verify equality
    assert isinstance(decoded, MarketTrade)
    assert decoded.symbol == original_event.symbol
    assert decoded.price == original_event.price
    assert decoded.timestamp == original_event.timestamp

    # 5. Verify polymorphism (BaseEvent type)
    assert isinstance(decoded, BaseEvent)

    print("\n[SUCCESS] Round-trip serialization verified!")


if __name__ == "__main__":
    main()
