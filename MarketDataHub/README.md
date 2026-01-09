# MarketDataHub

Minimal data-plane service that publishes real-time market events over ZeroMQ and routes warm-path data to QuestDB (ILP), including microstructure features.

## Run

```bash
python -m MarketDataHub.hub
```

Control via environment variables documented in `MarketDataHub/config.py`.

## Warm path persistence

Set `MARKET_DATA_TSDB_ENABLED=1` and configure host/port/batching in `MarketDataHub/config.py` so `QuestILPWriter` writes `market_l1`, `bars_1s`, and `microstructure_v1` via ILP. The hub keeps data-plane feeds separate from trading logic; bots still subscribe only via ZMQ SUB.

## LockBot market-data contracts

See `MarketDataHub/docs/contracts_lockbot_md.md` for the full LockBotBTC data contract surface (raw streams + derived VWAP/AVWAP/liquidation heatmap).
