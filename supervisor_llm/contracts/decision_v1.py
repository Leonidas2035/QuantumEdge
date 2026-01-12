from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

try:
    import msgspec
except Exception:  # pragma: no cover - optional dependency
    msgspec = None

try:
    import orjson
except Exception:  # pragma: no cover - optional dependency
    orjson = None

ALLOWED_SIDES = {"BUY", "SELL", "HOLD", "REDUCE", "CLOSE"}
ALLOWED_RISK = {"LOW", "MED", "HIGH", "CRIT"}
REQUIRED_KEYS = {"v", "s", "c", "r", "rk"}
OPTIONAL_KEYS = {"sl", "tp"}
ALL_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS


class ValidationError(ValueError):
    pass


if msgspec:

    class DecisionV1Struct(msgspec.Struct, forbid_unknown_fields=True):
        v: int
        s: str
        c: float
        r: str
        rk: str
        sl: Optional[float] = None
        tp: Optional[float] = None


@dataclass(frozen=True)
class DecisionV1:
    v: int
    s: str
    c: float
    sl: Optional[float]
    tp: Optional[float]
    r: str
    rk: str

    def to_compact_json(self) -> str:
        payload = {
            "v": self.v,
            "s": self.s,
            "c": float(self.c),
            "sl": self.sl,
            "tp": self.tp,
            "r": self.r,
            "rk": self.rk,
        }
        if orjson:
            return orjson.dumps(payload).decode("utf-8")
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _parse_json_strict(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        raise ValidationError("empty_output")

    if msgspec:
        try:
            return msgspec.json.decode(raw)
        except Exception as exc:  # pragma: no cover - msgspec path
            raise ValidationError(f"json_decode:{exc}") from exc

    decoder = json.JSONDecoder()
    try:
        obj, idx = decoder.raw_decode(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"json_decode:{exc}") from exc
    if raw[idx:].strip():
        raise ValidationError("trailing_text")
    return obj


def _validate_keys(obj: Dict[str, Any]) -> None:
    keys = set(obj.keys())
    if keys - ALL_KEYS:
        extra = ",".join(sorted(keys - ALL_KEYS))
        raise ValidationError(f"extra_keys:{extra}")
    missing = REQUIRED_KEYS - keys
    if missing:
        raise ValidationError(f"missing_keys:{','.join(sorted(missing))}")


def _validate_value_constraints(obj: Dict[str, Any]) -> None:
    if obj["v"] != 1:
        raise ValidationError("invalid_version")
    if obj["s"] not in ALLOWED_SIDES:
        raise ValidationError("invalid_side")
    c_val = obj["c"]
    if not isinstance(c_val, (int, float)):
        raise ValidationError("invalid_confidence_type")
    if c_val < 0.0 or c_val > 1.0:
        raise ValidationError("invalid_confidence_range")

    for key in ("sl", "tp"):
        if key in obj:
            val = obj[key]
            if val is None:
                continue
            if not isinstance(val, (int, float)):
                raise ValidationError(f"invalid_{key}_type")
            if val <= 0:
                raise ValidationError(f"invalid_{key}_range")

    reason = obj["r"]
    if not isinstance(reason, str):
        raise ValidationError("invalid_reason_type")
    if len(reason) > 60:
        raise ValidationError("invalid_reason_length")
    if "\n" in reason or "\r" in reason:
        raise ValidationError("invalid_reason_newline")

    if obj["rk"] not in ALLOWED_RISK:
        raise ValidationError("invalid_risk")


def _coerce_to_decision(obj: Dict[str, Any]) -> DecisionV1:
    return DecisionV1(
        v=int(obj["v"]),
        s=str(obj["s"]),
        c=float(obj["c"]),
        sl=obj.get("sl"),
        tp=obj.get("tp"),
        r=str(obj["r"]),
        rk=str(obj["rk"]),
    )


def decode_decision(raw: str) -> DecisionV1:
    if msgspec:
        try:
            struct = msgspec.json.decode(raw, type=DecisionV1Struct)
            obj = {
                "v": struct.v,
                "s": struct.s,
                "c": struct.c,
                "sl": struct.sl,
                "tp": struct.tp,
                "r": struct.r,
                "rk": struct.rk,
            }
        except Exception as exc:  # pragma: no cover - msgspec path
            message = str(exc)
            if "unknown field" in message:
                raise ValidationError("extra_keys") from exc
            if "missing required field" in message:
                raise ValidationError("missing_keys") from exc
            raise ValidationError(f"json_decode:{exc}") from exc
    else:
        obj = _parse_json_strict(raw)
        if not isinstance(obj, dict):
            raise ValidationError("not_object")

    _validate_keys(obj)
    _validate_value_constraints(obj)
    return _coerce_to_decision(obj)


def fallback_decision(reason: str = "parse_fail") -> DecisionV1:
    safe_reason = reason[:60].replace("\n", " ").replace("\r", " ")
    return DecisionV1(
        v=1,
        s="HOLD",
        c=0.0,
        sl=None,
        tp=None,
        r=safe_reason,
        rk="CRIT",
    )


def schema_dict() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["v", "s", "c", "r", "rk"],
        "properties": {
            "v": {"type": "integer", "enum": [1]},
            "s": {"type": "string", "enum": sorted(ALLOWED_SIDES)},
            "c": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "sl": {"type": ["number", "null"], "exclusiveMinimum": 0.0},
            "tp": {"type": ["number", "null"], "exclusiveMinimum": 0.0},
            "r": {"type": "string", "maxLength": 60, "pattern": "^[^\n\r]*$"},
            "rk": {"type": "string", "enum": sorted(ALLOWED_RISK)},
        },
    }


def compact_keys() -> Iterable[str]:
    return ["v", "s", "c", "sl", "tp", "r", "rk"]
