"""Binance USD-M futures executor (sync, testnet-ready)."""

from __future__ import annotations

import os
import time
from typing import Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from LockBotBTC.lockbot_btc.execution.base import CancelAllResult, CancelResult, ExecutionConfig, SubmitResult


class BinanceFuturesExecutor:
    def __init__(self, cfg: ExecutionConfig) -> None:
        self._cfg = cfg
        self._client: Optional[Client] = None

    def _ensure_client(self) -> Optional[Client]:
        if self._client:
            return self._client
        api_key = os.getenv(self._cfg.api_key_env)
        api_secret = os.getenv(self._cfg.api_secret_env)
        if not api_key or not api_secret:
            return None
        client = Client(api_key, api_secret)
        client.FUTURES_URL = self._cfg.base_url.rstrip("/") + "/fapi"
        client.FUTURES_TESTNET_URL = self._cfg.base_url.rstrip("/") + "/fapi"
        self._client = client
        return client

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        reduce_only: bool,
        client_order_id: str,
        price: Optional[float] = None,
        time_in_force: Optional[str] = None,
    ) -> SubmitResult:
        client = self._ensure_client()
        if not client:
            return SubmitResult(ok=False, client_order_id=client_order_id, error_code="missing_keys", retryable=False)
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": qty,
            "reduceOnly": reduce_only,
            "newClientOrderId": client_order_id,
            "recvWindow": self._cfg.recv_window,
        }
        if price is not None and order_type != "MARKET":
            params["price"] = price
            params["timeInForce"] = time_in_force or "GTC"
        try:
            response = self._submit_with_retries(client.futures_create_order, params)
            return SubmitResult(
                ok=True,
                client_order_id=str(response.get("clientOrderId") or client_order_id),
                order_id=str(response.get("orderId") or ""),
                status=str(response.get("status") or ""),
            )
        except BinanceAPIException as exc:
            return SubmitResult(
                ok=False,
                client_order_id=client_order_id,
                error_code=str(getattr(exc, "code", "api_error")),
                error_detail=str(exc),
                retryable=_is_retryable(exc),
            )
        except BinanceRequestException as exc:
            return SubmitResult(
                ok=False,
                client_order_id=client_order_id,
                error_code="request_error",
                error_detail=str(exc),
                retryable=True,
            )
        except Exception as exc:
            return SubmitResult(
                ok=False,
                client_order_id=client_order_id,
                error_code="unexpected",
                error_detail=str(exc),
                retryable=False,
            )

    def cancel_order(
        self, *, symbol: str, client_order_id: Optional[str] = None, order_id: Optional[str] = None
    ) -> CancelResult:
        client = self._ensure_client()
        if not client:
            return CancelResult(ok=False, client_order_id=client_order_id, order_id=order_id, error_code="missing_keys")
        try:
            params = {"symbol": symbol, "recvWindow": self._cfg.recv_window}
            if client_order_id:
                params["origClientOrderId"] = client_order_id
            if order_id:
                params["orderId"] = order_id
            response = self._submit_with_retries(client.futures_cancel_order, params)
            return CancelResult(
                ok=True,
                client_order_id=str(response.get("clientOrderId") or client_order_id or ""),
                order_id=str(response.get("orderId") or order_id or ""),
                status=str(response.get("status") or ""),
            )
        except BinanceAPIException as exc:
            return CancelResult(
                ok=False,
                client_order_id=client_order_id,
                order_id=order_id,
                error_code=str(getattr(exc, "code", "api_error")),
                error_detail=str(exc),
                retryable=_is_retryable(exc),
            )
        except BinanceRequestException as exc:
            return CancelResult(
                ok=False,
                client_order_id=client_order_id,
                order_id=order_id,
                error_code="request_error",
                error_detail=str(exc),
                retryable=True,
            )
        except Exception as exc:
            return CancelResult(
                ok=False,
                client_order_id=client_order_id,
                order_id=order_id,
                error_code="unexpected",
                error_detail=str(exc),
                retryable=False,
            )

    def cancel_all(self, *, symbol: str) -> CancelAllResult:
        client = self._ensure_client()
        if not client:
            return CancelAllResult(ok=False, error_code="missing_keys")
        try:
            response = self._submit_with_retries(client.futures_cancel_all_open_orders, {"symbol": symbol})
            return CancelAllResult(ok=True, status=str(response.get("msg") or "ok"))
        except BinanceAPIException as exc:
            return CancelAllResult(
                ok=False,
                error_code=str(getattr(exc, "code", "api_error")),
                error_detail=str(exc),
                retryable=_is_retryable(exc),
            )
        except BinanceRequestException as exc:
            return CancelAllResult(ok=False, error_code="request_error", error_detail=str(exc), retryable=True)
        except Exception as exc:
            return CancelAllResult(ok=False, error_code="unexpected", error_detail=str(exc), retryable=False)

    def _submit_with_retries(self, fn, params: dict, max_attempts: int = 3) -> dict:
        backoff = 0.4
        attempts = 0
        while True:
            try:
                return fn(**params)
            except (BinanceAPIException, BinanceRequestException) as exc:
                attempts += 1
                if attempts >= max_attempts or not _is_retryable(exc):
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 2.0)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, BinanceRequestException):
        return True
    if isinstance(exc, BinanceAPIException):
        code = getattr(exc, "code", None)
        if code is None:
            return False
        try:
            return int(code) in {-1003, -1013, -1021, -1100, -1101} or int(code) >= 500
        except Exception:
            return False
    return False
