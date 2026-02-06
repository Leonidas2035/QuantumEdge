from __future__ import annotations

import json

from model_router.router.distill import DistillConfig, DistillWriter
from model_router.router.redaction import redact_prompt


def test_distill_writer_redaction(tmp_path):
    path = tmp_path / "distill.jsonl"
    writer = DistillWriter(path, DistillConfig(enable=True, store_prompt=False))
    prompt_info = redact_prompt("Bearer sk-secret token=abc", store_prompt=False)

    student = {"ok": True, "decision": {"v": 1}, "raw_hash": "x"}
    teacher = {"ok": True, "decision": {"v": 1}, "raw_hash": "y"}
    diff = {"same_action": True, "confidence_delta": 0.0, "risk_delta": "LOW->LOW", "notes": "shadow"}
    meta = {"student_model": "s", "teacher_model": "t", "lat_ms_student": 1.0, "lat_ms_teacher": 2.0}

    writer.write(prompt_info, student, teacher, diff, meta)

    data = path.read_text(encoding="utf-8").strip()
    record = json.loads(data)
    assert record["prompt_hash"]
    assert "REDACTED" in record["prompt_redacted"]
