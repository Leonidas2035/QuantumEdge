"""User data stream listener for Binance spot account."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from quantum_edge_core.market_data.account.binance_userstream_base import (
    BinanceUserStreamBase,
)
from quantum_edge_core.market_data.config import AccountConfig


class BinanceSpotUserStream(BinanceUserStreamBase):
    STREAM_URL = "wss://stream.binance.com:9443/ws"

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
            f"{self._config.base_url}/api/v3/userDataStream",
            headers={"X-MBX-APIKEY": self._config.spot_api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json().get("listenKey", "")

    def _keepalive_listen_key(self, listen_key: str) -> None:
        resp = self._session.put(
            f"{self._config.base_url}/api/v3/userDataStream",
            params={"listenKey": listen_key},
            headers={"X-MBX-APIKEY": self._config.spot_api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def _keepalive_interval(self) -> float:
        return 30 * 60

    def _parse_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        evt = payload.get("e")
        if evt not in {"outboundAccountPosition", "executionReport"}:
            return None
        payload["src"] = "spot_ws"
        return payload
