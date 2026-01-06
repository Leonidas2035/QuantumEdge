"""Base helpers for Binance user data stream listeners."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional

import requests
import websockets


def _safe_json_decode(payload: str) -> Dict[str, Any]:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


class BinanceUserStreamBase(ABC):
    """Shared helper for Binance user-data listeners."""

    def __init__(
        self,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        session: Optional[requests.Session] = None,
        timeout: float = 10.0,
        on_reconnect: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._session = session or requests.Session()
        self._handler = handler
        self._timeout = timeout
        self._stop_event: Optional[asyncio.Event] = None
        self._keepalive_task: Optional[asyncio.Task[Any]] = None
        self._on_reconnect = on_reconnect

    async def run(self) -> None:
        self._stop_event = asyncio.Event()
        backoff = 1.0
        while not self._stop_event.is_set():
            listen_key = await self._create_listen_key_async()
            if not listen_key:
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
                continue
            ws_url = self._ws_endpoint(listen_key)
            self._keepalive_task = asyncio.create_task(self._keepalive_loop(listen_key))
            try:
                async with websockets.connect(ws_url, ping_interval=20, close_timeout=5) as ws:
                    backoff = 1.0
                    if self._on_reconnect:
                        await self._on_reconnect()
                    await self._consume(ws)
            except Exception:
                backoff = min(backoff * 2, 60)
            finally:
                if self._keepalive_task:
                    self._keepalive_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._keepalive_task
                    self._keepalive_task = None
        await self._cleanup()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._keepalive_task:
            self._keepalive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._keepalive_task

    async def _consume(self, ws: websockets.WebSocketClientProtocol) -> None:
        async for message in ws:
            if self._stop_event and self._stop_event.is_set():
                break
            payload = _safe_json_decode(message)
            if not payload:
                continue
            event = self._parse_event(payload)
            if event:
                await self._handler(event)

    async def _keepalive_loop(self, listen_key: str) -> None:
        interval = self._keepalive_interval()
        while not (self._stop_event and self._stop_event.is_set()):
            await asyncio.sleep(interval)
            await self._run_in_executor(lambda: self._keepalive_listen_key(listen_key))

    async def _create_listen_key_async(self) -> str:
        return await self._run_in_executor(self._create_listen_key)

    async def _run_in_executor(self, func: Callable[[], Any]) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func)

    @abstractmethod
    def _ws_endpoint(self, listen_key: str) -> str:
        ...

    @abstractmethod
    def _create_listen_key(self) -> str:
        ...

    @abstractmethod
    def _keepalive_listen_key(self, listen_key: str) -> None:
        ...

    @abstractmethod
    def _keepalive_interval(self) -> float:
        ...

    @abstractmethod
    def _parse_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ...

    async def _cleanup(self) -> None:  # pragma: no cover
        return


class suppress:
    def __init__(self, *exceptions: type[Exception]) -> None:
        self._exc = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Optional[type], exc: Optional[Exception], tb: Optional[Any]) -> bool:
        return isinstance(exc, self._exc)
