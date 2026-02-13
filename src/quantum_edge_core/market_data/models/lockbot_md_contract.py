"""LockBot market-data contract constants and schema helpers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "lockbot_md.v1"

TOPIC_MARK_PRICE_1S = "mark_price_1s"
TOPIC_TRADES_AGG = "trades_agg"
TOPIC_OHLCV_1M = "ohlcv_1m"
TOPIC_OHLCV_5M = "ohlcv_5m"
TOPIC_OHLCV_15M = "ohlcv_15m"
TOPIC_FUNDING_RATE = "funding_rate"
TOPIC_FORCE_ORDER = "force_order"
TOPIC_VWAP_D = "vwap_d"
TOPIC_VWAP_BANDS_D = "vwap_bands_d"
TOPIC_AVWAP = "avwap"
TOPIC_LIQ_HEATMAP = "liq_heatmap"

LOCKBOT_TOPICS: Sequence[str] = (
    TOPIC_MARK_PRICE_1S,
    TOPIC_TRADES_AGG,
    TOPIC_OHLCV_1M,
    TOPIC_OHLCV_5M,
    TOPIC_OHLCV_15M,
    TOPIC_FUNDING_RATE,
    TOPIC_FORCE_ORDER,
    TOPIC_VWAP_D,
    TOPIC_VWAP_BANDS_D,
    TOPIC_AVWAP,
    TOPIC_LIQ_HEATMAP,
)

LOCKBOT_TABLE_MAP: Mapping[str, str] = {
    TOPIC_MARK_PRICE_1S: "lockbot_mark_price_1s",
    TOPIC_TRADES_AGG: "lockbot_trades_agg",
    TOPIC_OHLCV_1M: "lockbot_ohlcv_1m",
    TOPIC_OHLCV_5M: "lockbot_ohlcv_5m",
    TOPIC_OHLCV_15M: "lockbot_ohlcv_15m",
    TOPIC_FUNDING_RATE: "lockbot_funding_rate",
    TOPIC_FORCE_ORDER: "lockbot_force_order",
    TOPIC_VWAP_D: "lockbot_vwap_d",
    TOPIC_VWAP_BANDS_D: "lockbot_vwap_bands_d",
    TOPIC_AVWAP: "lockbot_avwap",
    TOPIC_LIQ_HEATMAP: "lockbot_liq_heatmap",
}

REQUIRED_ENVELOPE_FIELDS: Sequence[str] = (
    "schema",
    "topic",
    "symbol",
    "ts_event",
    "ts_pub",
    "source",
    "seq",
    "payload",
)

PAYLOAD_REQUIRED_FIELDS: Mapping[str, Sequence[str]] = {
    TOPIC_MARK_PRICE_1S: ("mark_price",),
    TOPIC_TRADES_AGG: ("price", "qty", "is_buyer_maker"),
    TOPIC_OHLCV_1M: ("open", "high", "low", "close", "volume", "interval", "bar_start_ts"),
    TOPIC_OHLCV_5M: ("open", "high", "low", "close", "volume", "interval", "bar_start_ts"),
    TOPIC_OHLCV_15M: ("open", "high", "low", "close", "volume", "interval", "bar_start_ts"),
    TOPIC_FUNDING_RATE: ("funding_rate", "funding_time"),
    TOPIC_FORCE_ORDER: ("side", "price", "qty", "ts_liq"),
    TOPIC_VWAP_D: ("session", "vwap", "pv_sum", "v_sum", "n_trades"),
    TOPIC_VWAP_BANDS_D: (
        "session",
        "vwap",
        "pv_sum",
        "v_sum",
        "n_trades",
        "std",
        "band_1u",
        "band_1l",
        "band_2u",
        "band_2l",
    ),
    TOPIC_AVWAP: ("anchors",),
    TOPIC_LIQ_HEATMAP: ("window_s", "bin_type", "bin_size", "decay", "levels", "last_force_order_ts"),
}


def validate_envelope(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_ENVELOPE_FIELDS:
        if field not in payload:
            errors.append(f"missing:{field}")
    if "schema" in payload and not isinstance(payload.get("schema"), str):
        errors.append("schema:type")
    if "topic" in payload and not isinstance(payload.get("topic"), str):
        errors.append("topic:type")
    if "symbol" in payload and not isinstance(payload.get("symbol"), str):
        errors.append("symbol:type")
    if "ts_event" in payload and not isinstance(payload.get("ts_event"), int):
        errors.append("ts_event:type")
    if "ts_pub" in payload and not isinstance(payload.get("ts_pub"), int):
        errors.append("ts_pub:type")
    if "source" in payload and not isinstance(payload.get("source"), str):
        errors.append("source:type")
    if "seq" in payload and not isinstance(payload.get("seq"), int):
        errors.append("seq:type")
    if "payload" in payload and not isinstance(payload.get("payload"), dict):
        errors.append("payload:type")
    return errors


def validate_payload(topic: str, payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = PAYLOAD_REQUIRED_FIELDS.get(topic, ())
    for field in required:
        if field not in payload:
            errors.append(f"missing_payload:{field}")
    return errors
