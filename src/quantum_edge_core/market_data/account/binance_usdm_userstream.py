"""User data stream listener for Binance USD-M futures account."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from quantum_edge_core.market_data.account.binance_userstream_base import BinanceUserStreamBase
from quantum_edge_core.market_data.config import AccountConfig


class BinanceUsdmUserStream(BinanceUserStreamBase):
    STREAM_URL = "wss://fstream.binance.com/ws"

    def __init__(
        self,
        config: AccountConfig,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        on_reconnect: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(handler, on_reconnect=on_reconnect)
        self._config = config

    def _ws_endpoint(self, listen_key: str) -> str:
        return f"{self.STREAM_URL}/{listen_key}"

    def _create_listen_key(self) -> str:
        resp = self._session.post(
            f"{self._config.fapi_url}/fapi/v1/listenKey",
            headers={"X-MBX-APIKEY": self._config.usdm_api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json().get("listenKey", "")

    def _keepalive_listen_key(self, listen_key: str) -> None:
        resp = self._session.put(
            f"{self._config.fapi_url}/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            headers={"X-MBX-APIKEY": self._config.usdm_api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def _keepalive_interval(self) -> float:
        return 30 * 60

    def _parse_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        evt = payload.get("e")
        if evt not in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE"}:
            return None
        payload["src"] = "usdm_ws"
        return payload
