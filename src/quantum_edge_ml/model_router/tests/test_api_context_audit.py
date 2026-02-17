from __future__ import annotations

import json

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient

import model_router.api.app as app_module
from model_router.contracts.decision_v1 import fallback_decision
from model_router.context.models import ContextPackV1


class FakeRouter:
    def __init__(self):
        self.last_prompt = None

    def route(self, prompt, timeout_s, hints=None):
        self.last_prompt = prompt
        decision = fallback_decision("ok")
        return type(
            "R",
            (),
            {
                "decision": decision,
                "backend": "student",
                "latency_ms": 1.0,
                "ok": True,
                "error": None,
            },
        )


class FakeFetcher:
    def get_context(self, symbol, lookback_m):
        return ContextPackV1(
            v=1,
            sym=symbol,
            lbm=lookback_m,
            t0="2025-01-01T00:00:00Z",
            t1="2025-01-01T00:15:00Z",
            ohlcv=[[1, 1, 1, 1, 1, 1]],
            chg=0.1,
            vol=0.2,
        )


class FakeAuditWriter:
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)


def test_api_with_context(monkeypatch):
    router = FakeRouter()
    fetcher = FakeFetcher()
    audit = FakeAuditWriter()

    monkeypatch.setattr(app_module, "_get_router", lambda: router)
    monkeypatch.setattr(app_module, "_get_context_fetcher", lambda: fetcher)
    monkeypatch.setattr(app_module, "_get_audit_writer", lambda: audit)

    client = TestClient(app_module.app)
    resp = client.post(
        "/v1/supervisor/decision_routed",
        json={
            "prompt": "check",
            "with_context": True,
            "symbol": "BTCUSDT",
            "lookback_m": 15,
        },
    )
    assert resp.status_code == 200
    payload = json.loads(resp.text)
    assert payload["v"] == 1
    assert router.last_prompt.startswith("CTX|")
    assert len(audit.events) == 1
