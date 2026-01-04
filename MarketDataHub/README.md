# MarketDataHub

Minimal data-plane service that publishes real-time market events over ZeroMQ and routes warm-path data to QuestDB (ILP).

## Run

```bash
python -m MarketDataHub.hub
```

Control via environment variables documented in `MarketDataHub/config.py`.

## Warm path persistence

Set `MARKET_DATA_TSDB_ENABLED=1` and configure host/port/batching in `MarketDataHub/config.py` so `QuestILPWriter` writes `market_l1` and `bars_1s` via ILP (`market_l1,symbol=...` lines). The hub keeps data-plane feeds separate from trading logic; bots still subscribe only via ZMQ SUB.
