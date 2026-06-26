"""Policy rollout/rollback manager for QuantumEdge policies."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PolicyRecord:
    policy_path: Path
    applied_at: str
    policy_hash: str
    status: str
    reason: str


class PolicyManager:
    def __init__(
        self,
        artifacts_dir: Path,
        runtime_dir: Path,
        history_dir: Path,
        history_keep: int = 5,
    ) -> None:
        self.artifacts_dir = artifacts_dir
        self.runtime_dir = runtime_dir
        self.history_dir = history_dir
        self.history_keep = max(int(history_keep), 1)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        (self.runtime_dir).mkdir(parents=True, exist_ok=True)

    def list_history(self) -> List[PolicyRecord]:
        history_path = self.history_dir / "history.jsonl"
        if not history_path.exists():
            return []
        records = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(
                PolicyRecord(
                    policy_path=Path(data.get("policy_path", "")),
                    applied_at=data.get("applied_at", ""),
                    policy_hash=data.get("policy_hash", ""),
                    status=data.get("status", ""),
                    reason=data.get("reason", ""),
                )
            )
        return records

    def current_policy(self) -> Optional[Path]:
        current_path = self.history_dir / "current.json"
        if not current_path.exists():
            return None
        try:
            payload = json.loads(current_path.read_text(encoding="utf-8"))
            value = payload.get("policy_path")
            return Path(value) if value else None
        except json.JSONDecodeError:
            return None

    def current_record(self) -> Optional[Dict[str, Any]]:
        current_path = self.history_dir / "current.json"
        if not current_path.exists():
            return None
        try:
            return json.loads(current_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def rollout(self, policy_path: Path, reason: str) -> Path:
        policy = self._load_policy(policy_path)
        errors = self.validate_policy(policy)
        if errors:
            raise ValueError("; ".join(errors))
        dest = self.runtime_dir / "current_policy.json"
        _atomic_write_json(dest, policy)
        record = PolicyRecord(
            policy_path=policy_path,
            applied_at=_iso_utc(),
            policy_hash=_hash_payload(policy),
            status="applied",
            reason=reason,
        )
        self._append_history(record)
        self._write_current(record)
        return dest

    def rollback(self, reason: str) -> Optional[Path]:
        history = self.list_history()
        if len(history) < 2:
            return None
        previous = history[-2]
        policy = self._load_policy(previous.policy_path)
        dest = self.runtime_dir / "current_policy.json"
        _atomic_write_json(dest, policy)
        record = PolicyRecord(
            policy_path=previous.policy_path,
            applied_at=_iso_utc(),
            policy_hash=_hash_payload(policy),
            status="rollback",
            reason=reason,
        )
        self._append_history(record)
        self._write_current(record)
        return dest

    def validate_policy(self, policy: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if not policy:
            errors.append("policy_empty")
            return errors
        horizons = policy.get("horizons")
        if horizons is not None and not isinstance(horizons, list):
            errors.append("horizons_invalid")
        thresholds = policy.get("thresholds")
        if thresholds is not None and not isinstance(thresholds, dict):
            errors.append("thresholds_invalid")
        if isinstance(thresholds, dict):
            for key, value in thresholds.items():
                try:
                    val = float(value)
                    if val < 0 or val > 1:
                        errors.append(f"threshold_out_of_range:{key}")
                except (TypeError, ValueError):
                    errors.append(f"threshold_invalid:{key}")
        if "schema_hash" not in policy:
            errors.append("schema_hash_missing")
        return errors

    def _load_policy(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Policy not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _append_history(self, record: PolicyRecord) -> None:
        history_path = self.history_dir / "history.jsonl"
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "policy_path": str(record.policy_path),
                        "applied_at": record.applied_at,
                        "policy_hash": record.policy_hash,
                        "status": record.status,
                        "reason": record.reason,
                    }
                )
                + "\n"
            )
        self._trim_history()

    def _write_current(self, record: PolicyRecord) -> None:
        payload = {
            "policy_path": str(record.policy_path),
            "applied_at": record.applied_at,
            "policy_hash": record.policy_hash,
            "status": record.status,
            "reason": record.reason,
        }
        _atomic_write_json(self.history_dir / "current.json", payload)

    def _trim_history(self) -> None:
        history_path = self.history_dir / "history.jsonl"
        lines = history_path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= self.history_keep:
            return
        trimmed = lines[-self.history_keep :]
        history_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")


def _hash_payload(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".tmp.", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_path).replace(path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
