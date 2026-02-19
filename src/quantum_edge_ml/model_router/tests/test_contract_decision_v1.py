from __future__ import annotations

import pytest
from model_router.contracts.decision_v1 import ValidationError, decode_decision


def test_rejects_extra_keys():
    raw = '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW","x":1}'
    with pytest.raises(ValidationError, match="extra_keys"):
        decode_decision(raw)


def test_missing_required_keys():
    raw = '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok"}'
    with pytest.raises(ValidationError, match="missing_keys"):
        decode_decision(raw)


def test_confidence_range():
    raw = '{"v":1,"s":"HOLD","c":1.5,"sl":null,"tp":null,"r":"ok","rk":"LOW"}'
    with pytest.raises(ValidationError, match="invalid_confidence_range"):
        decode_decision(raw)


def test_reason_length():
    raw = (
        '{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"'
        + "a" * 61
        + '","rk":"LOW"}'
    )
    with pytest.raises(ValidationError, match="invalid_reason_length"):
        decode_decision(raw)
