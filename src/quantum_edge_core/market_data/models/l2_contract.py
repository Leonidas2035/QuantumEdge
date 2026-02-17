"""Constants describing the canonical L2 JSONL contract."""

from __future__ import annotations

from typing import Mapping, Sequence

ENTITY_TABLE_MAP: Mapping[str, str] = {
    "fills": "l2_fills",
    "positions": "l2_positions",
    "equity": "l2_equity",
    "risk": "l2_risk",
}

ALLOWED_ENTITIES: Sequence[str] = tuple(ENTITY_TABLE_MAP.keys())

SCHEMA_VERSION: int = 1
STREAM_NAME = "l2"

REQUIRED_TOP_LEVEL_FIELDS: Sequence[str] = (
    "ts_ns",
    "stream",
    "entity",
    "schema_ver",
    "payload",
)
