"""Priority-aware event bus for MarketDataHub."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Callable, Deque, Dict, Iterable, Tuple

from MarketDataHub.models import MarketEvent, Priority


# Type for spool hook (called when L2 queue is full).
SpoolHook = Callable[[MarketEvent], None]


class EventBus:
    """In-memory event bus with bounded priority queues."""

    def __init__(
        self,
        *,
        l0_hwm: int = 2000,
        l1_hwm: int = 5000,
        l2_hwm: int = 1000,
        spool_hook: SpoolHook | None = None,
    ) -> None:
        self._queues: Dict[Priority, Deque[MarketEvent]] = {
            Priority.L0: deque(),
            Priority.L1: deque(),
            Priority.L2: deque(),
        }
        self._max_sizes = {
            Priority.L0: l0_hwm,
            Priority.L1: l1_hwm,
            Priority.L2: l2_hwm,
        }
        self._condition = asyncio.Condition()
        self._sequence: Dict[Tuple[str, str], int] = {}
        self._spool_hook = spool_hook or self._default_spool

    @property
    def last_sequence(self) -> Dict[Tuple[str, str], int]:
        return dict(self._sequence)

    def _default_spool(self, event: MarketEvent) -> None:
        logging.warning("L2 backlog full, event marked for spool: %s", event)

    def assign_sequence(self, symbol: str, event_type: str) -> int:
        key = (symbol, event_type)
        last = self._sequence.get(key, 0)
        next_seq = last + 1
        self._sequence[key] = next_seq
        return next_seq

    async def publish(self, event: MarketEvent) -> None:
        async with self._condition:
            queue = self._queues[event.priority]
            max_size = self._max_sizes[event.priority]
            if len(queue) >= max_size:
                if event.priority == Priority.L0:
                    dropped = queue.popleft()
                    logging.debug("Dropping L0 event due to HWM: %s", dropped)
                elif event.priority == Priority.L1:
                    dropped = queue.popleft()
                    logging.warning("Dropping oldest L1 event due to HWM: %s", dropped)
                else:
                    self._spool_hook(event)
            queue.append(event)
            logging.debug("Published event %s seq=%s to queue %s", event.event_type, event.seq, event.priority)
            self._condition.notify_all()

    async def get_event(
        self,
        *,  # only keyword args below
        priority_order: Iterable[Priority] | None = None,
    ) -> MarketEvent:
        order = tuple(priority_order or (Priority.L2, Priority.L1, Priority.L0))
        async with self._condition:
            while True:
                for priority in order:
                    queue = self._queues[priority]
                    if queue:
                        event = queue.popleft()
                        return event
                await self._condition.wait()
