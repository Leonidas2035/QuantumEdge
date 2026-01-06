"""Publish normalized account events over the shared ZMQ socket."""

from __future__ import annotations

import msgspec

from MarketDataHub.ipc.publisher import ZmqPublisher
from MarketDataHub.models.account_snapshot import AccountSnapshot
from MarketDataHub.models.account_delta import AccountDelta


class AccountPublisher:
    """Wraps the shared ZMQ publisher for account topics."""

    def __init__(self, publisher: ZmqPublisher) -> None:
        self._publisher = publisher

    def publish_snapshot(self, snapshot: AccountSnapshot) -> None:
        payload = msgspec.msgpack.encode(snapshot)
        self._publisher.publish_payload("account:snapshot", payload)

    def publish_delta(self, delta: AccountDelta) -> None:
        payload = msgspec.msgpack.encode(delta)
        topic = f"account:delta:{delta.src}"
        self._publisher.publish_payload(topic, payload)
