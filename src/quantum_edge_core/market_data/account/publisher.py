"""Publish normalized account events over the shared ZMQ socket."""

from __future__ import annotations

import msgspec
import json

from quantum_edge_core.market_data.ipc.publisher import ZmqPublisher
from quantum_edge_core.market_data.models.account_snapshot import AccountSnapshot
from quantum_edge_core.market_data.models.account_delta import AccountDelta


class AccountPublisher:
    """Wraps the shared ZMQ publisher for account topics."""

    def __init__(self, publisher: ZmqPublisher) -> None:
        self._publisher = publisher

    def publish_snapshot(self, snapshot: AccountSnapshot) -> None:
        payload = msgspec.msgpack.encode(snapshot)
        # Wrap payload into a dict with explicit type or just send directly to topic
        # To strictly follow event schema we structure it:
        wrapper = {
            "type": "hub.account_snapshot.v1",
            "data": json.loads(msgspec.json.encode(snapshot).decode("utf-8")),
        }
        self._publisher.publish_payload(
            "account:snapshot", msgspec.json.encode(wrapper)
        )

    def publish_delta(self, delta: AccountDelta) -> None:
        wrapper = {
            "type": "hub.account_delta.v1",
            "data": json.loads(msgspec.json.encode(delta).decode("utf-8")),
        }
        topic = f"account:delta:{delta.src}"
        self._publisher.publish_payload(topic, msgspec.json.encode(wrapper))
