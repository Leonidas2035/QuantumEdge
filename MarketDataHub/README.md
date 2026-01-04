# MarketDataHub

Minimal data-plane service that publishes real-time market events over ZeroMQ and routes warm-path data to QuestDB (ILP).

## Run

```bash
python -m MarketDataHub.hub
```

Control via environment variables documented in `MarketDataHub/config.py`.
