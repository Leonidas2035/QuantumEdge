"""In-process replay bus for topic-based pub/sub."""

from __future__ import annotations

from typing import Any, Callable, List, Tuple


Callback = Callable[[str, Any], None]


class ReplayBus:
    def __init__(self) -> None:
        self._subs: List[Tuple[str, Callback]] = []

    def subscribe(self, topic_prefix: str, callback: Callback) -> None:
        self._subs.append((topic_prefix, callback))

    def publish(self, topic: str, message: Any) -> None:
        for prefix, callback in list(self._subs):
            if topic.startswith(prefix):
                callback(topic, message)
