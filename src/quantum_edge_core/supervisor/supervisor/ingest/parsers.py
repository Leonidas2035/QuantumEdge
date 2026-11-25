"""Parsers for QuantumEdge runtime telemetry artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from quantum_edge_core.supervisor.supervisor.tsdb.base import Point


def parse_event_line(line: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if "type" not in payload:
        return None
    return payload


def event_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def event_to_point(event: Dict[str, Any], digest: str) -> Optional[Point]:
    ts = _parse_ts(event.get("ts_utc") or event.get("ts") or event.get("timestamp"))
    if ts is None:
        ts = datetime.now(timezone.utc)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    reason = _extract_reason_codes(data)
    latency = _extract_latency_ms(data)
    payload_json = json.dumps(data, separators=(",", ":")) if data else None
    return Point(
        measurement="qe_events",
        ts=ts,
        tags={
            "symbol": str(event.get("symbol") or "unknown"),
            "mode": str(event.get("mode") or "unknown"),
            "component": str(event.get("component") or "unknown"),
            "event_type": str(event.get("type") or "unknown"),
        },
        fields={
            "reason_codes": reason,
            "latency_ms": latency,
            "payload_json": payload_json,
            "run_id": event.get("run_id"),
            "event_hash": digest,
        },
    )


def parse_metrics_file(path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def metrics_to_point(payload: Dict[str, Any]) -> Optional[Point]:
    ts = _parse_ts(payload.get("ts") or payload.get("timestamp"))
    if ts is None:
        return None
    breaker = payload.get("breaker") if isinstance(payload.get("breaker"), dict) else {}
    rejects_top = payload.get("reject_top")
    return Point(
        measurement="qe_metrics",
        ts=ts,
        tags={
            "symbol": str(payload.get("symbol") or "unknown"),
            "mode": str(payload.get("mode") or "unknown"),
        },
        fields={
            "tick_age_ms": payload.get("tick_age_ms"),
            "book_age_ms": payload.get("book_age_ms"),
            "breakers_active": breaker.get("reason") if breaker.get("active") else None,
            "rejects_top": (
                json.dumps(rejects_top, separators=(",", ":")) if rejects_top else None
            ),
            "inference_p50_ms": payload.get("latency_p50_ms"),
            "inference_p95_ms": payload.get("latency_p95_ms"),
            "position_notional": payload.get("position_notional"),
            "policy_id": payload.get("ml_policy") or payload.get("policy_id"),
            "schema_hash": payload.get("schema_hash"),
            "error_code": payload.get("last_error"),
            "payload_json": json.dumps(payload, separators=(",", ":")),
        },
    )


def parse_exec_line(line: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if "type" not in payload:
        return None
    return payload


def exec_to_point(payload: Dict[str, Any]) -> Optional[Point]:
    ts = _parse_ts(payload.get("ts") or payload.get("timestamp"))
    if ts is None:
        ts = datetime.now(timezone.utc)
    return Point(
        measurement="qe_exec",
        ts=ts,
        tags={
            "symbol": str(payload.get("symbol") or "unknown"),
            "side": str(payload.get("side") or payload.get("direction") or "unknown"),
            "order_type": str(
                payload.get("order_type") or payload.get("type") or "unknown"
            ),
        },
        fields={
            "qty": payload.get("size") or payload.get("qty"),
            "price": payload.get("price"),
            "slippage_bps": payload.get("slippage_bps"),
            "fee_bps": payload.get("fee_bps"),
            "result": payload.get("result") or payload.get("status"),
            "client_order_id": payload.get("client_order_id"),
            "exchange_order_id": payload.get("exchange_order_id"),
            "payload_json": json.dumps(payload, separators=(",", ":")),
        },
    )


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        try:
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _extract_reason_codes(data: Dict[str, Any]) -> Optional[str]:
    if not data:
        return None
    reason_codes = data.get("reason_codes")
    if isinstance(reason_codes, list):
        return ",".join(str(code) for code in reason_codes)
    reason = data.get("reason")
    if reason:
        return str(reason)
    reasons = data.get("reasons")
    if isinstance(reasons, list):
        return ",".join(str(code) for code in reasons)
    return None


def _extract_latency_ms(data: Dict[str, Any]) -> Optional[float]:
    for key in ("latency_ms", "inference_ms", "loop_ms"):
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                return None
    return None
