# BTCUSDT Account Fields & Sources

| Field | Source | Notes |
| --- | --- | --- |
| `market.spot_last[].price` | `GET /api/v3/ticker/price?symbol=BTCUSDT` (REST) or `ws://.../bookTicker` (spot WS) | REST used only at startup/repair if `publish_market_prices` enabled. |
| `market.usdm_mark[].markPrice` / `fundingRate` | `GET /fapi/v1/premiumIndex?symbol=BTCUSDT` | Same REST-only principle. |
| `spot.balances[]` | `GET /api/v3/account?omitZeroBalances=true` | Built at startup/repair; deltas come from `outboundAccountPosition` (spot WS). |
| `spot.open_orders[]` | `GET /api/v3/openOrders?symbol=BTCUSDT` **(always include ?symbol)** | Subsequent order updates arrive via `executionReport`. |
| `usdm.account_totals` | `GET /fapi/v3/account` | Repaired and replaced wholly when triggers fire. |
| `usdm.assets[]` | `GET /fapi/v3/account` (field `assets`) or `ACCOUNT_UPDATE.B` (futures WS) | Delivers balances updates per asset. |
| `usdm.positions[]` | `GET /fapi/v3/positionRisk` or `ACCOUNT_UPDATE.P` | WS replaces only touched positions; account snapshot is authoritative after repairs. |
| `usdm.open_orders[]` | `GET /fapi/v1/openOrders?symbol=BTCUSDT` | Future order deltas (`ORDER_TRADE_UPDATE`) incrementally patch the cache. |

**Important**: calls to `/openOrders` **must always include `symbol=BTCUSDT`** to keep Binance weight low; we never query without a symbol. REST is limited to startup/repair, WS carries the live state.
