"""REST snapshot builder for Binance account data."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlencode

import requests

from MarketDataHub.config import AccountConfig
from MarketDataHub.models.account_delta import AccountDelta, DeltaPatch, SpotPatch, UsdmPatch
from MarketDataHub.models.account_snapshot import (
    AccountSnapshot,
    MarketBlock,
    MarketPriceEntry,
    SpotBlock,
    UsdmBlock,
    BalanceEntry,
    OpenOrderEntry,
    UsdmAccountTotals,
    UsdmAssetEntry,
    UsdmPositionEntry,
    UsdmMarkEntry,
)


def _mask_key(key: str) -> str:
    if not key:
        return "binance:unknown"
    return f"binance:{key[-4:]}"


def _ensure_str(value) -> str:
    return str(value) if value is not None else ""


class BinanceAccountRestSnapshotBuilder:
    """Build normalized account snapshot data from Binance REST."""

    def __init__(self, config: AccountConfig, session: Optional[requests.Session] = None):
        self._config = config
        self._session = session or requests.Session()
        self._timeout = 10.0

    def build_full_account_snapshot(
        self,
        symbols: List[str],
        include_market: bool = True,
        account_ref: Optional[str] = None,
    ) -> AccountSnapshot:
        ts_ms = int(time.time() * 1000)
        market_block = self.build_market_snapshot(symbols) if include_market else MarketBlock([], [])
        spot_block = self.build_spot_snapshot(symbols)
        usdm_block = self.build_usdm_snapshot(symbols)
        snapshot_account_ref = account_ref or _mask_key(self._config.spot_api_key)
        return AccountSnapshot(
            ts_ms=ts_ms,
            account_ref=snapshot_account_ref,
            symbols=symbols,
            market=market_block,
            spot=spot_block,
            usdm=usdm_block,
        )

    def build_spot_snapshot(self, symbols: List[str]) -> SpotBlock:
        account = self._signed_get(self._config.base_url, "/api/v3/account", {"omitZeroBalances": "true"}, self._config.spot_api_key, self._config.spot_api_secret)
        balances = [
            BalanceEntry(asset=item["asset"], free=_ensure_str(item["free"]), locked=_ensure_str(item["locked"]))
            for item in account.get("balances", [])
        ]
        open_orders = []
        for symbol in symbols:
            data = self._signed_get(self._config.base_url, "/api/v3/openOrders", {"symbol": symbol}, self._config.spot_api_key, self._config.spot_api_secret)
            for order in data:
                open_orders.append(self._normalize_order(order))
        return SpotBlock(balances=balances, open_orders=open_orders)

    def build_usdm_snapshot(self, symbols: List[str]) -> UsdmBlock:
        account = self._signed_get(self._config.fapi_url, "/fapi/v3/account", {}, self._config.usdm_api_key, self._config.usdm_api_secret)
        positions = self._signed_get(self._config.fapi_url, "/fapi/v3/positionRisk", {}, self._config.usdm_api_key, self._config.usdm_api_secret)
        open_orders = []
        for symbol in symbols:
            data = self._signed_get(self._config.fapi_url, "/fapi/v1/openOrders", {"symbol": symbol}, self._config.usdm_api_key, self._config.usdm_api_secret)
            for order in data:
                open_orders.append(self._normalize_order(order, include_future=True))
        totals = account
        assets = account.get("assets", [])
        assets_entries = [
            UsdmAssetEntry(asset=item["asset"], walletBalance=_ensure_str(item["walletBalance"]), availableBalance=_ensure_str(item["availableBalance"]))
            for item in assets
        ]
        position_entries = [
            UsdmPositionEntry(
                symbol=item["symbol"],
                positionSide=item["positionSide"],
                positionAmt=_ensure_str(item["positionAmt"]),
                entryPrice=_ensure_str(item["entryPrice"]),
                markPrice=_ensure_str(item["markPrice"]),
                unRealizedProfit=_ensure_str(item["unRealizedProfit"]),
                leverage=_ensure_str(item["leverage"]),
                marginType=item.get("marginType", ""),
                liquidationPrice=_ensure_str(item["liquidationPrice"]),
                notional=_ensure_str(item.get("notional", 0)),
            )
            for item in positions
            if item.get("symbol")
        ]
        totals_entry = UsdmAccountTotals(
            totalWalletBalance=_ensure_str(totals.get("totalWalletBalance", "")),
            totalUnrealizedProfit=_ensure_str(totals.get("totalUnrealizedProfit", "")),
            totalMarginBalance=_ensure_str(totals.get("totalMarginBalance", "")),
            availableBalance=_ensure_str(totals.get("availableBalance", "")),
            maxWithdrawAmount=_ensure_str(totals.get("maxWithdrawAmount", "")),
        )
        return UsdmBlock(
            account_totals=totals_entry,
            assets=assets_entries,
            positions=position_entries,
            open_orders=open_orders,
        )

    def build_market_snapshot(self, symbols: List[str]) -> MarketBlock:
        spot_last = []
        usdm_mark = []
        for symbol in symbols:
            ticker = self._get(self._config.base_url, "/api/v3/ticker/price", {"symbol": symbol})
            spot_last.append(MarketPriceEntry(symbol=symbol, price=_ensure_str(ticker.get("price", "")), ts_ms=int(time.time() * 1000), src="spot_rest_ticker_price"))
            premium = self._get(self._config.fapi_url, "/fapi/v1/premiumIndex", {"symbol": symbol})
            if isinstance(premium, list) and premium:
                premium = premium[0]
            usdm_mark.append(
                UsdmMarkEntry(
                    symbol=symbol,
                    markPrice=_ensure_str(premium.get("markPrice", "")),
                    fundingRate=_ensure_str(premium.get("lastFundingRate", "")),
                    ts_ms=int(time.time() * 1000),
                    src="usdm_rest_premiumIndex",
                )
            )
        return MarketBlock(spot_last=spot_last, usdm_mark=usdm_mark)

    def _normalize_order(self, order: dict, include_future: bool = False) -> OpenOrderEntry:
        return OpenOrderEntry(
            symbol=order["symbol"],
            orderId=str(order["orderId"]),
            clientOrderId=order["clientOrderId"],
            status=order["status"],
            side=order["side"],
            type=order["type"],
            price=_ensure_str(order.get("price", "")),
            origQty=_ensure_str(order.get("origQty", "")),
            executedQty=_ensure_str(order.get("executedQty", "")),
            cummulativeQuoteQty=_ensure_str(order.get("cummulativeQuoteQty", "")),
            timeInForce=order.get("timeInForce", ""),
            eventTime=order.get("eventTime"),
            transactTime=order.get("transactTime"),
        )

    def _get(self, base: str, path: str, params: dict) -> dict:
        resp = self._session.get(f"{base}{path}", params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _signed_get(self, base: str, path: str, params: dict, api_key: str, api_secret: str) -> dict:
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": self._config.recv_window}
        query = urlencode(sorted(params.items()))
        signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        headers = {"X-MBX-APIKEY": api_key}
        resp = self._session.get(f"{base}{path}", params=params, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()
