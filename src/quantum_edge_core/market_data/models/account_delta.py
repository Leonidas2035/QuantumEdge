"""Account delta contract for MarketDataHub."""

from __future__ import annotations

from typing import List, Optional

import msgspec


class BalancePatch(msgspec.Struct):
    asset: str
    free: str
    locked: str


class OrderPatch(msgspec.Struct):
    symbol: str
    orderId: str
    clientOrderId: str
    status: str
    side: str
    type: str
    price: str
    origQty: str
    executedQty: str
    cummulativeQuoteQty: Optional[str] = None
    avgPrice: Optional[str] = None
    reduceOnly: Optional[bool] = None
    positionSide: Optional[str] = None
    timeInForce: Optional[str] = None
    eventTime: Optional[int] = None
    transactTime: Optional[int] = None


class AccountTotalsPatch(msgspec.Struct):
    totalWalletBalance: str
    totalUnrealizedProfit: str
    totalMarginBalance: str
    availableBalance: str
    maxWithdrawAmount: str


class AssetPatch(msgspec.Struct):
    asset: str
    walletBalance: str
    availableBalance: str


class PositionPatch(msgspec.Struct):
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


class SpotPatch(msgspec.Struct):
    """Minimal spot delta patch (UserDataStream events)."""

    balances_update: Optional[List[BalancePatch]] = (
        None  # triggered by outboundAccountPosition
    )
    orders_update: Optional[List[OrderPatch]] = None  # triggered by executionReport


class UsdmPatch(msgspec.Struct):
    """Minimal USD-M delta patch (ACCOUNT_UPDATE, ORDER_TRADE_UPDATE)."""

    account_update: Optional[AccountTotalsPatch] = (
        None  # from ACCOUNT_UPDATE accountTotals
    )
    assets_update: Optional[List[AssetPatch]] = None  # ACCOUNT_UPDATE assets
    positions_update: Optional[List[PositionPatch]] = None  # ACCOUNT_UPDATE positions
    orders_update: Optional[List[OrderPatch]] = None  # ORDER_TRADE_UPDATE data


class DeltaPatch(msgspec.Struct):
    spot: Optional[SpotPatch] = None
    usdm: Optional[UsdmPatch] = None


class AccountDelta(msgspec.Struct, kw_only=True):
    type: str = "hub.account_delta"
    schema: str = "hub.account_delta.v1"
    ts_ms: int
    account_ref: str
    symbol: Optional[str] = None
    src: str
    seq: Optional[int] = None
    patch: DeltaPatch
