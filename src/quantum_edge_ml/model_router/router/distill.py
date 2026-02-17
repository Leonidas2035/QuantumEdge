from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from model_router.router.redaction import RedactionResult


@dataclass
class DistillConfig:
    enable: bool
    store_prompt: bool


class DistillWriter:
    def __init__(self, path: Path, config: DistillConfig) -> None:
        self.path = path
        self.config = config
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _raw_hash(self, raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def write(
        self,
        prompt_info: RedactionResult,
        student: Dict,
        teacher: Dict,
        diff: Dict,
        backend_meta: Dict,
    ) -> None:
        if not self.config.enable:
            return
        entry = {
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "schema": "DecisionV1",
            "prompt_hash": prompt_info.prompt_hash,
            "prompt_redacted": prompt_info.prompt_redacted,
            "student": student,
            "teacher": teacher,
            "diff": diff,
            "backend_meta": backend_meta,
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
            )

    def make_payload(self, ok: bool, decision_json: str, raw_text: str) -> Dict:
        payload = {
            "ok": ok,
            "decision": json.loads(decision_json),
            "raw_hash": self._raw_hash(raw_text),
        }
        return payload
