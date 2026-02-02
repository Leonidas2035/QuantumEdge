# LockBotBTC MarketDataHub Subscriptions (BTCUSDT)

LockBotBTC consumes MarketDataHub streams only (no direct Binance market-data calls). Subscribe to the following topics:

Raw streams:
- `BTCUSDT:mark_price_1s`
- `BTCUSDT:trades_agg`
- `BTCUSDT:ohlcv_1m`
- `BTCUSDT:ohlcv_5m`
- `BTCUSDT:ohlcv_15m`
- `BTCUSDT:funding_rate`
- `BTCUSDT:force_order`

Derived streams:
- `BTCUSDT:vwap_d`
- `BTCUSDT:vwap_bands_d`
- `BTCUSDT:avwap`
- `BTCUSDT:liq_heatmap`

Contract reference:
- `MarketDataHub/docs/contracts_lockbot_md.md`

