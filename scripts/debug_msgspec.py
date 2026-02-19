from typing import Union

import msgspec


class Base(msgspec.Struct, tag=True):
    pass


class Child(Base):
    x: int


Event = Union[Base, Child]


def run():
    enc = msgspec.json.Encoder()
    dec = msgspec.json.Decoder(Event)

    obj = Child(x=1)
    data = enc.encode(obj)
    print(f"Data: {data}")

    try:
        res = dec.decode(data)
        print(f"Decoded: {res}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    run()
