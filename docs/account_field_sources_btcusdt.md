# Binance account field sources — BTCUSDT

| Field | Description | Source |
| --- | --- | --- |
| `market.spot_last` | Current spot ticker price | `spot_ws` (bookTicker) or fallback `spot_rest_ticker_price` |
| `market.usdm_mark` | Mark price + funding rate | `usdm_rest_premiumIndex` |
| `spot.balances` | `asset` balances (free/locked) | `hub.account_snapshot` from `GET /api/v3/account` snapshot; incremental via `spot_ws` `outboundAccountPosition` |
| `spot.open_orders` | Spot open orders for BTCUSDT | REST `GET /api/v3/openOrders?symbol=BTCUSDT`; ws deltas via `executionReport` patch |
| `usdm.account_totals` | wallet/available/UNP totals | `GET /fapi/v3/account`; repaired after reconnect via `usdm_ws` `ACCOUNT_UPDATE` |
| `usdm.assets` | Per-asset wallet/available balances | `GET /fapi/v3/account` + ongoing `ACCOUNT_UPDATE` |
| `usdm.positions` | Positions per symbol | `GET /fapi/v3/positionRisk`; incremental via `ACCOUNT_UPDATE` |
| `usdm.open_orders` | USD-M open orders for BTCUSDT | `GET /fapi/v1/openOrders?symbol=BTCUSDT`; deltas via `ORDER_TRADE_UPDATE` |

## Delta patch rules

- `spot_ws` `outboundAccountPosition` feeds `spot.balances` updates; patch carries `balances_update` array with `asset/free/locked`.  
- `spot_ws` `executionReport` fills `spot.open_orders` patches; include all REST order fields plus event/transact times when present.  
- `usdm_ws` `ACCOUNT_UPDATE` updates `usdm.account_totals`, `usdm.assets`, and `usdm.positions`.  
- `usdm_ws` `ORDER_TRADE_UPDATE` updates `usdm.open_orders` via `orders_update`.

**Notes**

- `openOrders` endpoints always include `?symbol=BTCUSDT` to keep Binance weight low.  
- Delta `patch` objects only include the fields needed to update the cache (see models + patch rule summary above).  
- Repair snapshots (`*_rest_repair`) use the same REST endpoints but run only after reconnect/repair interval.
