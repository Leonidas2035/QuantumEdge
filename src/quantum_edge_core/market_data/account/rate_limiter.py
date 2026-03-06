"""Binance REST API Rate Limiter & Priority Request Queue.

Reads ``x-mbx-used-weight-1m`` from every Binance response and
auto-throttles when weight exceeds 75 % of the 2 400 limit.

Usage::

    limiter = BinanceRateLimiter()
    async with limiter:
        data = await limiter.get(url, params, headers)
        data = await limiter.signed_get(url, params, headers)

The :class:`PriorityRequestQueue` wraps the limiter and ensures
requests execute sequentially (≥100 ms apart) with priority ordering:

    HIGH   → order execution / cancellation
    MEDIUM → account / position sync
    LOW    → exchange info / history (startup only)
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

import aiohttp

logger: logging.Logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Binance weight constants ────────────────────────────────────────
_WEIGHT_LIMIT: int = 2400  # Binance default per-minute IP weight
_THROTTLE_PCT: float = 0.75  # Start throttling at 75 %
_THROTTLE_THRESHOLD: int = int(_WEIGHT_LIMIT * _THROTTLE_PCT)  # 1 800
_INTER_REQUEST_DELAY_S: float = 0.1  # Min gap between sequential REST calls


class RequestPriority(enum.IntEnum):
    """Lower value = higher priority."""

    HIGH = 0  # Order send / cancel
    MEDIUM = 1  # Account sync, position sync
    LOW = 2  # Exchange info, historical klines


@dataclass(order=True)
class _QueueItem:
    priority: int
    seq: int = field(compare=True)
    coro_factory: Callable[[], Awaitable[Any]] = field(compare=False)
    future: asyncio.Future[Any] = field(compare=False)


class BinanceRateLimiter:
    """Async HTTP client wrapper with Binance weight tracking.

    Reads ``x-mbx-used-weight-1m`` header from every response.
    When the used weight exceeds :data:`_THROTTLE_THRESHOLD`
    (75 % of 2 400 = 1 800), the limiter sleeps until the start
    of the next calendar minute before issuing the next request.
    """

    def __init__(
        self,
        weight_limit: int = _WEIGHT_LIMIT,
        throttle_pct: float = _THROTTLE_PCT,
        timeout: float = 10.0,
    ) -> None:
        self._weight_limit: int = weight_limit
        self._throttle_threshold: int = int(weight_limit * throttle_pct)
        self._timeout: float = timeout

        # State
        self._used_weight: int = 0
        self._last_weight_ts: float = 0.0
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Context manager ──────────────────────────────────────────────

    async def __aenter__(self) -> "BinanceRateLimiter":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Public API ───────────────────────────────────────────────────

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Unsigned GET with rate limiting."""
        async with self._lock:
            await self._maybe_throttle()
            session = self._ensure_session()
            async with session.get(url, params=params, headers=headers) as resp:
                self._read_weight(resp)
                resp.raise_for_status()
                return await resp.json()

    async def signed_get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """Signed GET with rate limiting (same transport, caller handles HMAC)."""
        async with self._lock:
            await self._maybe_throttle()
            session = self._ensure_session()
            async with session.get(url, params=params, headers=headers) as resp:
                self._read_weight(resp)
                resp.raise_for_status()
                return await resp.json()

    # ── Weight tracking ──────────────────────────────────────────────

    def _read_weight(self, resp: aiohttp.ClientResponse) -> None:
        """Extract ``x-mbx-used-weight-1m`` from response headers."""
        raw: str = resp.headers.get("x-mbx-used-weight-1m", "")
        if not raw:
            # Futures API uses a different header name
            raw = resp.headers.get("X-MBX-USED-WEIGHT-1M", "")
        if raw:
            try:
                self._used_weight = int(raw)
                self._last_weight_ts = time.monotonic()
            except ValueError:
                pass

        logger.debug(
            "Binance weight: %d / %d (threshold=%d)",
            self._used_weight,
            self._weight_limit,
            self._throttle_threshold,
        )

    async def _maybe_throttle(self) -> None:
        """If weight exceeds threshold, sleep until the next calendar minute."""
        # Reset weight if more than 60s have passed (Binance resets per minute)
        if time.monotonic() - self._last_weight_ts > 60.0:
            self._used_weight = 0
            return

        if self._used_weight >= self._throttle_threshold:
            # Calculate seconds until next minute boundary
            now: float = time.time()
            seconds_into_minute: float = now % 60
            sleep_sec: float = max(60.0 - seconds_into_minute + 1.0, 1.0)

            logger.info(
                "⏸ RATE LIMITER: weight %d/%d (%.0f%% of limit). "
                "Throttling for %.1fs until next minute.",
                self._used_weight,
                self._weight_limit,
                (self._used_weight / self._weight_limit) * 100,
                sleep_sec,
            )
            await asyncio.sleep(sleep_sec)
            self._used_weight = 0
            logger.info("▶ RATE LIMITER: Throttle released, resuming requests.")

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        return self._session

    @property
    def used_weight(self) -> int:
        return self._used_weight

    @property
    def weight_limit(self) -> int:
        return self._weight_limit


class PriorityRequestQueue:
    """Sequential REST request queue with priority ordering.

    Ensures ≥100 ms between consecutive Binance REST calls.
    Higher-priority requests (e.g. order execution) are served first.
    """

    def __init__(self, limiter: BinanceRateLimiter) -> None:
        self._limiter: BinanceRateLimiter = limiter
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._seq: int = 0
        self._worker_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Start the background worker that drains the queue."""
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def submit(
        self,
        priority: RequestPriority,
        coro_factory: Callable[[], Awaitable[T]],
    ) -> T:
        """Submit a request and wait for the result.

        Parameters
        ----------
        priority:
            Execution priority (HIGH > MEDIUM > LOW).
        coro_factory:
            Zero-arg callable that returns the awaitable to execute.
            Must be a factory (lambda / functools.partial) — NOT a
            pre-created coroutine, because we only want to create it
            when the worker is ready to execute it.

        Returns
        -------
        The result of the awaitable.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        self._seq += 1
        item = _QueueItem(
            priority=priority.value,
            seq=self._seq,
            coro_factory=coro_factory,
            future=future,
        )
        await self._queue.put(item)
        return await future

    async def _worker(self) -> None:
        """Drain the queue sequentially with inter-request delay."""
        while True:
            item: _QueueItem = await self._queue.get()
            try:
                result = await item.coro_factory()
                item.future.set_result(result)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()
                # Minimum gap between consecutive REST calls
                await asyncio.sleep(_INTER_REQUEST_DELAY_S)
