import sys
import logging

logging.basicConfig(level=logging.INFO)

sys.path.append("src")

from hermes.supervisor.market_client import fetch_multi_timeframe

data = fetch_multi_timeframe("BTCUSDT", ("5m",), 1)
print(data)
