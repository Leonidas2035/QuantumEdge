from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class EventPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class EventBusStats:
    published: int = 0
    dropped: int = 0
    dropped_low: int = 0
    dropped_normal: int = 0
    dropped_high: int = 0


class EventBus:
    """Priority event bus with bounded queue + drop policy."""

    def __init__(
        self,
        max_events: int = 10000,
        max_bytes: int = 256 * 1024 * 1024,
        drop_policy: str = "drop_lowest",
    ) -> None:
        self._queues = {
            EventPriority.HIGH: asyncio.Queue(maxsize=max_events),
            EventPriority.NORMAL: asyncio.Queue(maxsize=max_events),
            EventPriority.LOW: asyncio.Queue(maxsize=max_events),
        }
        self._max_events = max(int(max_events), 1)
        self._max_bytes = max(int(max_bytes), 1024)
        self._drop_policy = (
            drop_policy
            if drop_policy in {"drop_lowest", "drop_newest"}
            else "drop_lowest"
        )
        self._lock = asyncio.Lock()
        self._events = 0
        self._bytes = 0
        self.stats = EventBusStats()

    @staticmethod
    def _estimate_size(event: Dict[str, Any]) -> int:
        try:
            return len(json.dumps(event, separators=(",", ":"), default=str))
        except Exception:
            return 256

    def _record_drop(self, priority: EventPriority) -> None:
        self.stats.dropped += 1
        if priority == EventPriority.LOW:
            self.stats.dropped_low += 1
        elif priority == EventPriority.NORMAL:
            self.stats.dropped_normal += 1
        else:
            self.stats.dropped_high += 1

    def _drop_one_lowest_locked(self) -> bool:
        for priority in (EventPriority.LOW, EventPriority.NORMAL, EventPriority.HIGH):
            queue = self._queues[priority]
            try:
                _, size = queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
            self._events = max(self._events - 1, 0)
            self._bytes = max(self._bytes - size, 0)
            self._record_drop(priority)
            return True
        return False

    async def publish(self, event: Dict[str, Any], priority: EventPriority = EventPriority.NORMAL) -> bool:
        size = self._estimate_size(event)
        async with self._lock:
            over_limit = self._events >= self._max_events or (self._bytes + size) > self._max_bytes
            if over_limit and self._drop_policy == "drop_newest":
                self._record_drop(priority)
                return False
            while over_limit and self._events > 0:
                if not self._drop_one_lowest_locked():
                    break
                over_limit = self._events >= self._max_events or (self._bytes + size) > self._max_bytes
            if self._events >= self._max_events or (self._bytes + size) > self._max_bytes:
                self._record_drop(priority)
                return False
            queue = self._queues.get(priority, self._queues[EventPriority.NORMAL])
            try:
                queue.put_nowait((event, size))
            except asyncio.QueueFull:
                if self._drop_policy == "drop_newest":
                    self._record_drop(priority)
                    return False
                if not self._drop_one_lowest_locked():
                    self._record_drop(priority)
                    return False
                try:
                    queue.put_nowait((event, size))
                except asyncio.QueueFull:
                    self._record_drop(priority)
                    return False
            self._events += 1
            self._bytes += size
            self.stats.published += 1
            return True

    async def get(self) -> Dict[str, Any]:
        while True:
            for priority in (EventPriority.HIGH, EventPriority.NORMAL, EventPriority.LOW):
                queue = self._queues[priority]
                if not queue.empty():
                    event, size = await queue.get()
                    async with self._lock:
                        self._events = max(self._events - 1, 0)
                        self._bytes = max(self._bytes - size, 0)
                    return event

            tasks = [asyncio.create_task(q.get()) for q in self._queues.values()]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            event, size = next(iter(done)).result()
            async with self._lock:
                self._events = max(self._events - 1, 0)
                self._bytes = max(self._bytes - size, 0)
            return event

    async def drain(self, max_items: int) -> Tuple[Dict[str, Any], ...]:
        items = []
        for _ in range(max_items):
            for priority in (EventPriority.HIGH, EventPriority.NORMAL, EventPriority.LOW):
                queue = self._queues[priority]
                if not queue.empty():
                    event, size = queue.get_nowait()
                    async with self._lock:
                        self._events = max(self._events - 1, 0)
                        self._bytes = max(self._bytes - size, 0)
                    items.append(event)
                    break
            else:
                break
        return tuple(items)

    def snapshot(self) -> Dict[str, int]:
        return {
            "events": self._events,
            "bytes": self._bytes,
            "published": self.stats.published,
            "dropped": self.stats.dropped,
        }
