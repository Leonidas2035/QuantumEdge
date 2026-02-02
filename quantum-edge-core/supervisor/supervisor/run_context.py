"""Run context + per-run artifacts for SupervisorAgent."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(ts: Optional[datetime] = None) -> str:
    value = ts or _utc_now()
    return value.isoformat().replace("+00:00", "Z")


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        sha = (result.stdout or "").strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"


def _git_dirty(repo_root: Path) -> Optional[bool]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return bool((result.stdout or "").strip())
    except Exception:
        return None


def _redact(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            key_str = str(key)
            lower = key_str.lower()
            if any(token in lower for token in ("key", "secret", "pass", "token")):
                redacted[key_str] = "***REDACTED***"
            else:
                redacted[key_str] = _redact(value)
        return redacted
    if isinstance(obj, (list, tuple)):
        return [_redact(item) for item in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _json_safe(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(item) for item in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(_json_safe(item) for item in obj)
    return obj


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EventsWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            json.dump(event, handle, ensure_ascii=False)
            handle.write("\n")


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    git_commit: str
    git_dirty: Optional[bool]
    policy_version: str
    model_version: str
    supervisor_version: str
    episode_set: Optional[str]
    episode_id: Optional[str]
    scenario_id: Optional[str]
    note: Optional[str]
    host: str
    platform: str
    start_ts_utc: str
    config_hash: Optional[str] = None
    errors_count: int = 0
    events_writer: EventsWriter = field(init=False)

    @classmethod
    def create(
        cls,
        project_root: Path,
        policy_version: str,
        model_version: str,
        supervisor_version: Optional[str] = None,
        episode_set: Optional[str] = None,
        episode_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> "RunContext":
        now = _utc_now()
        git_commit = _git_commit(project_root)
        git_short = git_commit[:7] if git_commit and git_commit != "unknown" else "nogit"
        run_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{git_short}"
        run_dir = project_root / "runtime" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        supervisor_version = supervisor_version or (git_commit if git_commit != "unknown" else "dev")
        ctx = cls(
            run_id=run_id,
            run_dir=run_dir,
            git_commit=git_commit,
            git_dirty=_git_dirty(project_root),
            policy_version=policy_version,
            model_version=model_version,
            supervisor_version=supervisor_version,
            episode_set=episode_set,
            episode_id=episode_id,
            scenario_id=scenario_id,
            note=note,
            host=socket.gethostname(),
            platform=sys.platform,
            start_ts_utc=_iso_utc(now),
        )
        ctx.events_writer = EventsWriter(run_dir / "events.jsonl")
        return ctx

    def _breadcrumbs(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ts_utc": _iso_utc(),
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "config_hash": self.config_hash,
            "policy_version": self.policy_version,
            "model_version": self.model_version,
            "supervisor_version": self.supervisor_version,
            "episode_set": self.episode_set,
            "episode_id": self.episode_id,
            "scenario_id": self.scenario_id,
            "note": self.note,
            "host": self.host,
            "platform": self.platform,
        }

    def write_config_snapshot(self, config_payload: Dict[str, Any]) -> None:
        safe_payload = _json_safe(_redact(config_payload))
        self.config_hash = _stable_hash(safe_payload)
        snapshot = {
            **self._breadcrumbs(),
            "config": safe_payload,
        }
        path = self.run_dir / "config_snapshot.json"
        path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    def log_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event = {
            **self._breadcrumbs(),
            "type": event_type,
            "payload": payload or {},
        }
        self.events_writer.append(event)

    def log_error(self, exc: BaseException) -> None:
        self.errors_count += 1
        trace = traceback.format_exc()
        payload = {
            "exception_type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": trace[-4000:] if trace else "",
        }
        error_path = self.run_dir / "errors.log"
        line = f"{_iso_utc()} {payload['exception_type']}: {payload['message']}\n"
        with error_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self.log_event("ERROR", payload)

    def write_summary(self, summary_payload: Dict[str, Any]) -> None:
        payload = {
            **self._breadcrumbs(),
            **_json_safe(summary_payload),
        }
        path = self.run_dir / "summary.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def find_incomplete_previous_run(self) -> Optional[Dict[str, Any]]:
        parent = self.run_dir.parent
        if not parent.exists():
            return None
        candidates = sorted([p for p in parent.iterdir() if p.is_dir()], reverse=True)
        for path in candidates:
            if path.name == self.run_id:
                continue
            summary_path = path / "summary.json"
            if not summary_path.exists():
                return {"run_id": path.name, "path": str(path)}
            break
        return None

    def update_directives(self, directives: Dict[str, Any], runtime_root: Path) -> None:
        content = {
            **self._breadcrumbs(),
            **_json_safe(directives),
        }
        run_path = self.run_dir / "directives.json"
        _atomic_write_json(run_path, content)

        stable_dir = runtime_root / "directives"
        stable_dir.mkdir(parents=True, exist_ok=True)
        stable_path = stable_dir / "latest.json"
        _atomic_write_json(stable_path, content)

    def write_artifacts_manifest(self) -> None:
        entries = []
        for item in sorted(self.run_dir.iterdir()):
            if item.name == "artifacts.json" or item.is_dir():
                continue
            stat = item.stat()
            entry = {
                "name": item.name,
                "size_bytes": stat.st_size,
                "mtime_utc": _iso_utc(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
            }
            if item.name in {"summary.json", "config_snapshot.json"}:
                entry["sha256"] = _sha256_file(item)
            entries.append(entry)
        payload = {
            **self._breadcrumbs(),
            "artifacts": entries,
        }
        path = self.run_dir / "artifacts.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
