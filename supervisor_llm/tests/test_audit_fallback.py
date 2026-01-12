from __future__ import annotations

import json
from supervisor_llm.audit.events import AuditEvent
from supervisor_llm.audit.questdb_ilp import QuestDBAuditWriter


def test_audit_fallback_when_ilp_missing(monkeypatch, tmp_path):
    writer = QuestDBAuditWriter()
    writer.fallback_path = tmp_path / "audit_fallback.jsonl"

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("questdb"):
            raise ImportError("no questdb")
        return orig_import(name, globals, locals, fromlist, level)

    import builtins

    orig_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", fake_import)

    event = AuditEvent(
        ts_utc="2025-01-01T00:00:00Z",
        sym="BTCUSDT",
        backend_used="student",
        model_id="local",
        mode="local_first",
        decision_json='{"v":1}',
        confidence=0.1,
        risk="LOW",
        latency_ms=1.0,
        prompt_hash="abc",
        ok=True,
        error_code=None,
    )
    writer.write(event)

    data = writer.fallback_path.read_text(encoding="utf-8").strip()
    record = json.loads(data)
    assert record["reason"] == "ilp_missing"
