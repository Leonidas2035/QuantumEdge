# LockBotBTC IPC Topics (lockbot_control.v1)

## Subscribed topics

MarketDataHub:
- `BTCUSDT:mark_price_1s`
- `BTCUSDT:vwap_d`
- `BTCUSDT:vwap_bands_d`
- `BTCUSDT:avwap`
- `BTCUSDT:liq_heatmap`

Supervisor commands:
- `LOCKBOT:BTCUSDT:cmd`

Account topics (optional, configurable):
- Use `account_topics` in `LockBotBTC/config/lockbot_btc.yaml` to map to existing Hub account feeds.
- Recommended for reconciliation:
  - `account:snapshot`
  - `account:delta:usdm`

## Published topics

- `LOCKBOT:BTCUSDT:ack`
- `LOCKBOT:BTCUSDT:status`
- `LOCKBOT:BTCUSDT:exec`

## Schema

See `LockBotBTC/lockbot/contracts/lockbot_control_v1.py` and `LockBotBTC/lockbot/contracts/lockbot_exec_v1.py`.
