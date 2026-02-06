"""Account snapshot contract for MarketDataHub."""

from __future__ import annotations

from typing import List

import msgspec


class MarketPriceEntry(msgspec.Struct):
    symbol: str
    price: str
    ts_ms: int
    src: str


class UsdmMarkEntry(msgspec.Struct):
    symbol: str
    markPrice: str
    fundingRate: str
    ts_ms: int
    src: str


class BalanceEntry(msgspec.Struct):
    asset: str
    free: str
    locked: str


class OpenOrderEntry(msgspec.Struct):
    symbol: str
    orderId: str
    clientOrderId: str
    status: str
    side: str
    type: str
    price: str
    origQty: str
    executedQty: str
    cummulativeQuoteQty: str
    timeInForce: str
    eventTime: int | None = None
    transactTime: int | None = None


class UsdmAccountTotals(msgspec.Struct):
    totalWalletBalance: str
    totalUnrealizedProfit: str
    totalMarginBalance: str
    availableBalance: str
    maxWithdrawAmount: str


class UsdmAssetEntry(msgspec.Struct):
    asset: str
    walletBalance: str
    availableBalance: str


class UsdmPositionEntry(msgspec.Struct):
    symbol: str
    positionSide: str
    positionAmt: str
    entryPrice: str
    markPrice: str
    unRealizedProfit: str
    leverage: str
    marginType: str
    liquidationPrice: str
    notional: str


class MarketBlock(msgspec.Struct):
    spot_last: List[MarketPriceEntry]
    usdm_mark: List[UsdmMarkEntry]


class SpotBlock(msgspec.Struct):
    balances: List[BalanceEntry]
    open_orders: List[OpenOrderEntry]


class UsdmBlock(msgspec.Struct):
    account_totals: UsdmAccountTotals
    assets: List[UsdmAssetEntry]
    positions: List[UsdmPositionEntry]
    open_orders: List[OpenOrderEntry]


class AccountSnapshot(msgspec.Struct, kw_only=True):
    type: str = "hub.account_snapshot"
    schema: str = "hub.account_snapshot.v1"
    ts_ms: int
    account_ref: str
    symbols: List[str]
    market: MarketBlock
    spot: SpotBlock
    usdm: UsdmBlock
