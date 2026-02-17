"""Account state cache + delta generation for MarketDataHub."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from quantum_edge_core.market_data.account.rest_snapshot import (
    BinanceAccountRestSnapshotBuilder,
)
from quantum_edge_core.market_data.config import AccountConfig
from quantum_edge_core.market_data.models.account_delta import (
    AccountDelta,
    AccountTotalsPatch,
    AssetPatch,
    BalancePatch,
    DeltaPatch,
    OrderPatch,
    PositionPatch,
    SpotPatch,
    UsdmPatch,
)
from quantum_edge_core.market_data.models.account_snapshot import (
    AccountSnapshot,
    OpenOrderEntry,
)

FINAL_ORDER_STATUSES = {"CANCELED", "FILLED", "EXPIRED", "REJECTED"}


def _mask_key(key: str) -> str:
    if not key:
        return "binance:unknown"
    return f"binance:{key[-4:]}"


def _ensure_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class AccountState:
    """Canonical account cache used by MarketDataHub."""

    def __init__(
        self,
        config: AccountConfig,
        rest_builder: Optional[BinanceAccountRestSnapshotBuilder] = None,
        account_ref: Optional[str] = None,
    ) -> None:
        self._config = config
        self._rest_builder = rest_builder or BinanceAccountRestSnapshotBuilder(config)
        self._account_ref = account_ref or _mask_key(
            config.spot_api_key or config.usdm_api_key
        )
        self._seq: Dict[str, int] = {}
        self.spot_balances: Dict[str, Dict[str, str]] = {}
        self.spot_open_orders: Dict[str, OrderPatch] = {}
        self.usdm_account_totals: Optional[AccountTotalsPatch] = None
        self.usdm_assets: Dict[str, AssetPatch] = {}
        self.usdm_positions: Dict[Tuple[str, str], PositionPatch] = {}
        self.usdm_open_orders: Dict[str, OrderPatch] = {}
        self.last_snapshot: Optional[AccountSnapshot] = None

    def build_snapshot(
        self,
        symbols: List[str],
        include_market: bool = True,
        account_ref: Optional[str] = None,
    ) -> AccountSnapshot:
        snapshot_ref = account_ref or self._account_ref
        snapshot = self._rest_builder.build_full_account_snapshot(
            symbols, include_market, snapshot_ref
        )
        self.apply_snapshot(snapshot)
        return snapshot

    def apply_snapshot(
        self, snapshot: AccountSnapshot, src: str = "rest_repair"
    ) -> AccountSnapshot:
        self.spot_balances = {
            entry.asset: {"free": entry.free, "locked": entry.locked}
            for entry in snapshot.spot.balances
        }
        self.spot_open_orders = {
            order.orderId: self._order_entry_to_patch(order)
            for order in snapshot.spot.open_orders
        }
        totals = snapshot.usdm.account_totals
        self.usdm_account_totals = AccountTotalsPatch(
            totalWalletBalance=totals.totalWalletBalance,
            totalUnrealizedProfit=totals.totalUnrealizedProfit,
            totalMarginBalance=totals.totalMarginBalance,
            availableBalance=totals.availableBalance,
            maxWithdrawAmount=totals.maxWithdrawAmount,
        )
        self.usdm_assets = {
            asset.asset: AssetPatch(
                asset=asset.asset,
                walletBalance=asset.walletBalance,
                availableBalance=asset.availableBalance,
            )
            for asset in snapshot.usdm.assets
        }
        self.usdm_positions = {
            (pos.symbol, pos.positionSide): PositionPatch(
                symbol=pos.symbol,
                positionSide=pos.positionSide,
                positionAmt=pos.positionAmt,
                entryPrice=pos.entryPrice,
                markPrice=pos.markPrice,
                unRealizedProfit=pos.unRealizedProfit,
                leverage=pos.leverage,
                marginType=pos.marginType,
                liquidationPrice=pos.liquidationPrice,
                notional=pos.notional,
            )
            for pos in snapshot.usdm.positions
        }
        self.usdm_open_orders = {
            order.orderId: self._order_entry_to_patch(order)
            for order in snapshot.usdm.open_orders
        }
        self.last_snapshot = snapshot
        return snapshot

    def apply_spot_outboundAccountPosition(
        self, event: Dict[str, Any]
    ) -> Optional[AccountDelta]:
        patches: List[BalancePatch] = []
        for entry in event.get("B", []):
            patch = BalancePatch(
                asset=entry.get("a", ""),
                free=_ensure_str(entry.get("f")),
                locked=_ensure_str(entry.get("l")),
            )
            self.spot_balances[patch.asset] = {
                "free": patch.free,
                "locked": patch.locked,
            }
            patches.append(patch)
        if not patches:
            return None
        return self._build_delta(
            src="spot_ws",
            spot_patch=SpotPatch(balances_update=patches),
            ts_ms=event.get("E"),
        )

    def apply_spot_execution_report(
        self, event: Dict[str, Any]
    ) -> Optional[AccountDelta]:
        raw_order = event.get("o") or event
        patch = self._build_order_patch(raw_order)
        if not patch.orderId:
            return None
        status = patch.status
        if status in FINAL_ORDER_STATUSES:
            self.spot_open_orders.pop(patch.orderId, None)
        else:
            self.spot_open_orders[patch.orderId] = patch
        return self._build_delta(
            src="spot_ws",
            spot_patch=SpotPatch(orders_update=[patch]),
            symbol=patch.symbol,
            ts_ms=event.get("E"),
        )

    def apply_usdm_ACCOUNT_UPDATE(
        self, event: Dict[str, Any]
    ) -> Optional[AccountDelta]:
        account = event.get("a", {})
        account_patch = AccountTotalsPatch(
            totalWalletBalance=_ensure_str(account.get("totalWalletBalance", "")),
            totalUnrealizedProfit=_ensure_str(account.get("totalUnrealizedProfit", "")),
            totalMarginBalance=_ensure_str(account.get("totalMarginBalance", "")),
            availableBalance=_ensure_str(account.get("availableBalance", "")),
            maxWithdrawAmount=_ensure_str(account.get("maxWithdrawAmount", "")),
        )
        assets = [
            AssetPatch(
                asset=entry.get("asset", ""),
                walletBalance=_ensure_str(entry.get("walletBalance")),
                availableBalance=_ensure_str(entry.get("availableBalance")),
            )
            for entry in event.get("B", [])
        ]
        positions = [
            PositionPatch(
                symbol=pos.get("symbol", ""),
                positionSide=pos.get("positionSide", ""),
                positionAmt=_ensure_str(pos.get("positionAmt")),
                entryPrice=_ensure_str(pos.get("entryPrice")),
                markPrice=_ensure_str(pos.get("markPrice")),
                unRealizedProfit=_ensure_str(pos.get("unRealizedProfit")),
                leverage=_ensure_str(pos.get("leverage")),
                marginType=pos.get("marginType", ""),
                liquidationPrice=_ensure_str(pos.get("liquidationPrice")),
                notional=_ensure_str(pos.get("notional")),
            )
            for pos in event.get("P", [])
        ]
        self.usdm_account_totals = account_patch
        for asset in assets:
            self.usdm_assets[asset.asset] = asset
        for pos in positions:
            self.usdm_positions[(pos.symbol, pos.positionSide)] = pos
        patch = DeltaPatch(
            usdm=UsdmPatch(
                account_update=account_patch,
                assets_update=assets or None,
                positions_update=positions or None,
            )
        )
        return self._build_delta(
            src="usdm_ws", usdm_patch=patch.usdm, ts_ms=event.get("E")
        )

    def apply_usdm_ORDER_TRADE_UPDATE(
        self, event: Dict[str, Any]
    ) -> Optional[AccountDelta]:
        raw_order = event.get("o") or event
        patch = self._build_order_patch(raw_order)
        if not patch.orderId:
            return None
        if patch.status in FINAL_ORDER_STATUSES:
            self.usdm_open_orders.pop(patch.orderId, None)
        else:
            self.usdm_open_orders[patch.orderId] = patch
        return self._build_delta(
            src="usdm_ws",
            usdm_patch=UsdmPatch(orders_update=[patch]),
            symbol=patch.symbol,
            ts_ms=event.get("E"),
        )

    def _build_delta(
        self,
        *,
        src: str,
        spot_patch: SpotPatch | None = None,
        usdm_patch: UsdmPatch | None = None,
        symbol: Optional[str] = None,
        ts_ms: Optional[int] = None,
    ) -> Optional[AccountDelta]:
        patch = DeltaPatch(spot=spot_patch, usdm=usdm_patch)
        if not (spot_patch or usdm_patch):
            return None
        return AccountDelta(
            ts_ms=int(ts_ms or time.time() * 1000),
            account_ref=self._account_ref,
            symbol=symbol,
            src=src,
            seq=self._next_seq(src),
            patch=patch,
        )

    def _next_seq(self, src: str) -> int:
        self._seq[src] = self._seq.get(src, 0) + 1
        return self._seq[src]

    def _build_order_patch(self, raw: Dict[str, Any]) -> OrderPatch:
        return OrderPatch(
            symbol=raw.get("symbol", ""),
            orderId=_ensure_str(raw.get("orderId")),
            clientOrderId=_ensure_str(raw.get("clientOrderId")),
            status=_ensure_str(raw.get("status")),
            side=_ensure_str(raw.get("side")),
            type=_ensure_str(raw.get("type")),
            price=_ensure_str(raw.get("price")),
            origQty=_ensure_str(raw.get("origQty")),
            executedQty=_ensure_str(raw.get("executedQty")),
            cummulativeQuoteQty=_ensure_str(raw.get("cummulativeQuoteQty")),
            avgPrice=_ensure_str(raw.get("avgPrice")),
            reduceOnly=raw.get("reduceOnly"),
            positionSide=_ensure_str(raw.get("positionSide")),
            timeInForce=_ensure_str(raw.get("timeInForce")),
            eventTime=raw.get("eventTime"),
            transactTime=raw.get("transactTime"),
        )

    def _order_entry_to_patch(self, entry: OpenOrderEntry) -> OrderPatch:
        return OrderPatch(
            symbol=entry.symbol,
            orderId=entry.orderId,
            clientOrderId=entry.clientOrderId,
            status=entry.status,
            side=entry.side,
            type=entry.type,
            price=entry.price,
            origQty=entry.origQty,
            executedQty=entry.executedQty,
            cummulativeQuoteQty=entry.cummulativeQuoteQty,
            avgPrice=None,
            reduceOnly=None,
            positionSide=None,
            timeInForce=entry.timeInForce,
            eventTime=entry.eventTime,
            transactTime=entry.transactTime,
        )
