## Account Mode: Binance BTCUSDT (Spot + USD-M)

MarketDataHub operates in a “data plane only” mode for account telemetry:

- **Startup:** call `BinanceAccountRestSnapshotBuilder.build_full_account_snapshot(symbols=["BTCUSDT"])` once, publish `hub.account_snapshot.v1`, and initialize the cache (balances, orders, positions).
- **Steady state:** listen to Binance user streams (spot/outboundAccountPosition + futures/ACCOUNT_UPDATE and ORDER_TRADE_UPDATE). Normalize every payload into `AccountDelta` patches and publish them via the existing bus so bots consume a single schema (`hub.account_delta.v1`).
- **Repair:** only call REST (`/api/v3/account`, `/api/v3/openOrders?symbol=BTCUSDT`, `/fapi/v3/account`, `/fapi/v3/positionRisk`, `/fapi/v1/openOrders?symbol=BTCUSDT`) when:
  * a user stream reconnect is detected,
  * the delta refers to an order that reached a final status but is missing from the cache,
  * or the configurable repair timer (default 900–1800 s) fires.
- **Weight discipline:** REST endpoints are expensive; we never poll them. WS deltas keep the cache fresh, the timer merely verifies liveness, and repairs are debounced so multiple triggers merge into a single REST call.
- **New symbol onboarding:** add the symbol to `HubConfig.symbols` and `AccountConfig` remains unchanged—openOrders and snapshots run per symbol, so no code path needs editing.

### Config snippet

```bash
export MARKET_DATA_ACCOUNT_SPOT=1
export MARKET_DATA_ACCOUNT_USDM=1
export BINANCE_ACCOUNT_REPAIR_INTERVAL=1800
export MARKET_DATA_ACCOUNT_MARKET_PRICES=1
```
