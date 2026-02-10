# LockBot Market-Data Contracts (lockbot_md.v1)

This document defines the MarketDataHub data contract surface required by LockBotBTC (BTCUSDT). All events use a common envelope and are published over ZeroMQ with topic `SYMBOL:<stream>`. MarketDataHub can also forward these events to QuestDB ILP using deterministic tables.

## Common Envelope (all topics)

Required fields:
- `schema`: `lockbot_md.v1`
- `topic`: `<SYMBOL>:<stream>`
- `symbol`: e.g., `BTCUSDT`
- `ts_event`: event time (ms)
- `ts_pub`: publish time (ms)
- `source`: `binance_ws` | `binance_rest` | `hub_derived`
- `seq`: monotonic per-topic (best effort)
- `payload`: topic-specific object

## Topics (BTCUSDT)

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

## Payload Schemas

`mark_price_1s` payload:
- `mark_price`: float
- `index_price`: float (optional)
- `funding_rate`: float (optional)
- `next_funding_time`: ms (optional)

`trades_agg` payload:
- `price`: float
- `qty`: float
- `is_buyer_maker`: bool
- `agg_trade_id`: int (optional)

`ohlcv_*` payload:
- `open`: float
- `high`: float
- `low`: float
- `close`: float
- `volume`: float
- `interval`: `1m` | `5m` | `15m`
- `bar_start_ts`: ms

`funding_rate` payload:
- `funding_rate`: float
- `funding_time`: ms

`force_order` payload:
- `side`: `BUY` | `SELL`
- `price`: float
- `qty`: float
- `order_status`: string (optional)
- `ts_liq`: ms

`vwap_d` payload:
- `session`: `{ "type": "UTC_DAY", "start_ts": ms, "end_ts": ms }`
- `vwap`: float
- `pv_sum`: float
- `v_sum`: float
- `n_trades`: int
- `session_reset`: bool (true on boundary)

`vwap_bands_d` payload:
- All fields from `vwap_d`
- `std`: float
- `band_1u`, `band_1l`, `band_2u`, `band_2l`: float
- `method`: `"weighted_variance"`

`avwap` payload:
- `anchors`: list of
  - `anchor_id`: `lock_entry` | `trend_start` | `liq_sweep` | `<custom>`
  - `anchor_ts`: ms
  - `vwap`: float
  - `pv_sum`: float
  - `v_sum`: float
  - `n_trades`: int

`liq_heatmap` payload:
- `window_s`: int
- `bin_type`: `fixed_price` (current implementation)
- `bin_size`: float
- `decay`: `{ "type": "exp", "half_life_s": int }`
- `levels`: array of `{ "price": float, "intensity": float, "side": "BUY"|"SELL", "n": int }`
- `intensity_above`, `intensity_below`: float (optional summary around last price)
- `last_force_order_ts`: ms

## Update Cadence
- `mark_price_1s`: expected 1s cadence (when source feed active)
- `trades_agg`: per trade/aggTrade tick
- `ohlcv_*`: emitted on bar close
- `funding_rate`: per funding event
- `vwap_d`, `vwap_bands_d`: configurable (default 1s), per trade update cadence
- `avwap`: configurable (default 1s)
- `liq_heatmap`: configurable (default 2s or on-change)

## VWAP Session Reset
- Session resets at 00:00 UTC.
- On reset, vwap accumulators are cleared and `session_reset=true` is emitted.

## AVWAP Anchors
- `lock_entry` is anchored to the UTC day start by default.
- `trend_start` and `liq_sweep` are disabled until set via the engine hook.
- Anchors are independent and update only with trades at or after their anchor timestamp.

## Liquidation Heatmap Proxy
- Heatmap is derived from observed `force_order` liquidation prints.
- It is not a forecast of future liquidation levels; it is a decayed histogram of realized liquidation activity.

## QuestDB Warm Path (ILP)
Tables (symbol tag included):
- `lockbot_mark_price_1s`
- `lockbot_trades_agg`
- `lockbot_ohlcv_1m`
- `lockbot_ohlcv_5m`
- `lockbot_ohlcv_15m`
- `lockbot_funding_rate`
- `lockbot_force_order`
- `lockbot_vwap_d`
- `lockbot_vwap_bands_d`
- `lockbot_avwap`
- `lockbot_liq_heatmap`

