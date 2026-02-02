"""Smoke utility that pushes warm-path rows via QuestDB ILP and verifies via HTTP /exec."""

import socket
import time
from urllib import parse, request

from market_data.config import TsdbConfig


def _send_lines(config: TsdbConfig, lines: list[str]) -> None:
    with socket.create_connection((config.host, config.ilp_port), timeout=5) as sock:
        payload = "\n".join(lines).encode("utf-8") + b"\n"
        sock.sendall(payload)


def _query_count(host: str, query: str) -> None:
    encoded = parse.quote(query, safe="")
    url = f"http://{host}:9000/exec?query={encoded}"
    with request.urlopen(url, timeout=5) as resp:
        data = resp.read().decode("utf-8")
    print("QuestDB /exec response:", data)


def main() -> None:
    config = TsdbConfig()
    lines = [
        "market_l1,symbol=BTCUSDT bid=1.0,ask=1.2,bid_sz=0.5,ask_sz=0.5 1700000000000000000",
        "bars_1s,symbol=BTCUSDT open=1.0,high=1.1,low=0.9,close=1.05,volume=100.0,trades=10i 1700000000000000000",
    ]
    _send_lines(config, lines)
    time.sleep(0.5)
    _query_count(config.host, "select count() from market_l1")
    _query_count(config.host, "select count() from bars_1s")


if __name__ == "__main__":
    main()
