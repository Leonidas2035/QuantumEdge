## Account Event Contracts

### `hub.account_snapshot.v1`

```json
{
  "type": "hub.account_snapshot",
  "schema": "hub.account_snapshot.v1",
  "ts_ms": 1700000000000,
  "account_ref": "binance:*4KEY",
  "symbols": ["BTCUSDT"],
  "market": {
    "spot_last": [{"symbol": "BTCUSDT", "price": "40050.25", "ts_ms": 1700000000000, "src": "spot_rest_ticker_price"}],
    "usdm_mark": [{"symbol": "BTCUSDT", "markPrice": "40048.12", "fundingRate": "0.0002", "ts_ms": 1700000000000, "src": "usdm_rest_premiumIndex"}]
  },
  "spot": {
    "balances": [{"asset": "BTC", "free": "0.50000000", "locked": "0.00000000"}],
    "open_orders": [{
      "symbol": "BTCUSDT",
      "orderId": "123456",
      "clientOrderId": "spot-1",
      "status": "NEW",
      "side": "BUY",
      "type": "LIMIT",
      "price": "40000.00",
      "origQty": "0.01",
      "executedQty": "0.000",
      "cummulativeQuoteQty": "0",
      "timeInForce": "GTC"
    }]
  },
  "usdm": {
    "account_totals": {
      "totalWalletBalance": "2.0000",
      "totalUnrealizedProfit": "0.1234",
      "totalMarginBalance": "2.1234",
      "availableBalance": "1.5000",
      "maxWithdrawAmount": "1.4000"
    },
    "assets": [{"asset": "BTC", "walletBalance": "0.050", "availableBalance": "0.050"}],
    "positions": [{
      "symbol": "BTCUSDT",
      "positionSide": "LONG",
      "positionAmt": "0.01",
      "entryPrice": "40000",
      "markPrice": "40010",
      "unRealizedProfit": "0.10",
      "leverage": "10",
      "marginType": "ISOLATED",
      "liquidationPrice": "35000",
      "notional": "400"
    }],
    "open_orders": []
  }
}
```

### `hub.account_delta.v1` examples

- **Spot balance delta (`outboundAccountPosition`):**

```json
{
  "type": "hub.account_delta",
  "schema": "hub.account_delta.v1",
  "ts_ms": 1700000000000,
  "account_ref": "binance:*4KEY",
  "src": "spot_ws",
  "patch": {
    "spot": {
      "balances_update": [{"asset": "USDT", "free": "1000.00", "locked": "0.00"}]
    }
  }
}
```

- **Spot order delta (`executionReport`):**

```json
{
  "type": "hub.account_delta",
  "schema": "hub.account_delta.v1",
  "ts_ms": 1700000001000,
  "account_ref": "binance:*4KEY",
  "symbol": "BTCUSDT",
  "src": "spot_ws",
  "patch": {
    "spot": {
      "orders_update": [{
        "symbol": "BTCUSDT",
        "orderId": "123456",
        "status": "NEW",
        "side": "BUY",
        "price": "40000.00",
        "origQty": "0.01"
      }]
    }
  }
}
```

- **USD-M account-update delta (`ACCOUNT_UPDATE`):**

```json
{
  "type": "hub.account_delta",
  "schema": "hub.account_delta.v1",
  "ts_ms": 1700000003000,
  "account_ref": "binance:*4KEY",
  "src": "usdm_ws",
  "patch": {
    "usdm": {
      "account_update": {
        "totalWalletBalance": "2.0000",
        "totalUnrealizedProfit": "0.1234",
        "totalMarginBalance": "2.1234",
        "availableBalance": "1.5000",
        "maxWithdrawAmount": "1.4000"
      },
      "positions_update": [{
        "symbol": "BTCUSDT",
        "positionSide": "LONG",
        "positionAmt": "0.01",
        "entryPrice": "40000",
        "markPrice": "40010",
        "unRealizedProfit": "0.10",
        "leverage": "10",
        "marginType": "ISOLATED",
        "liquidationPrice": "35000",
        "notional": "400"
      }]
    }
  }
}
```

- **USD-M order delta (`ORDER_TRADE_UPDATE`):**

```json
{
  "type": "hub.account_delta",
  "schema": "hub.account_delta.v1",
  "ts_ms": 1700000004000,
  "account_ref": "binance:*4KEY",
  "symbol": "BTCUSDT",
  "src": "usdm_ws",
  "patch": {
    "usdm": {
      "orders_update": [{
        "symbol": "BTCUSDT",
        "orderId": "200001",
        "status": "FILLED",
        "side": "SELL",
        "price": "41000.00",
        "origQty": "0.005",
        "executedQty": "0.005"
      }]
    }
  }
}
```
