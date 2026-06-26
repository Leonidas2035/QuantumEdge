"""Policy versioning and activation helpers for Ops Brain."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


@dataclass
class PolicyVersion:
    version_id: str
    policy_path: Path
    manifest_path: Path


def load_policy(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Policy must be a mapping: {path}")
    return data


def load_active_policy(
    runtime_dir: Path, fallback_path: Path
) -> Tuple[Dict[str, Any], str, Path]:
    active_path = runtime_dir / "policy_versions" / "active_policy.yaml"
    if active_path.exists():
        manifest_path = runtime_dir / "policy_versions" / "active_policy_manifest.json"
        version_id = "active"
        if manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                version_id = str(payload.get("version_id") or version_id)
            except json.JSONDecodeError:
                version_id = "active"
        return load_policy(active_path), version_id, active_path
    return load_policy(fallback_path), "base", fallback_path


def save_new_policy(
    policy: Dict[str, Any],
    runtime_dir: Path,
    project_root: Path,
    reason: str,
    source_run_id: Optional[str],
    previous_policy: Optional[Dict[str, Any]] = None,
    previous_version_id: Optional[str] = None,
) -> PolicyVersion:
    versions_dir = runtime_dir / "policy_versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_id = _next_version_id(versions_dir)
    policy_path = versions_dir / f"policy_{version_id}.yaml"
    manifest_path = versions_dir / f"policy_{version_id}_manifest.json"

    _atomic_write_yaml(policy_path, policy)
    diff_summary = _diff_dicts(previous_policy or {}, policy)
    manifest = {
        "version_id": version_id,
        "created_at": _iso_utc(),
        "reason": reason,
        "source_run_id": source_run_id,
        "previous_version_id": previous_version_id,
        "config_hash": _stable_hash(policy),
        "git_commit": _git_commit(project_root),
        "git_dirty": _git_dirty(project_root),
        "diff_summary": diff_summary,
    }
    _atomic_write_json(manifest_path, manifest)
    return PolicyVersion(
        version_id=version_id, policy_path=policy_path, manifest_path=manifest_path
    )


def activate_policy(runtime_dir: Path, version_id: str, source: str = "manual") -> Path:
    versions_dir = runtime_dir / "policy_versions"
    policy_path = versions_dir / f"policy_{version_id}.yaml"
    if not policy_path.exists():
        raise FileNotFoundError(f"Policy version not found: {policy_path}")
    active_path = versions_dir / "active_policy.yaml"
    active_manifest_path = versions_dir / "active_policy_manifest.json"
    _atomic_copy(policy_path, active_path)
    manifest = {
        "version_id": version_id,
        "activated_at": _iso_utc(),
        "source": source,
        "policy_path": str(policy_path),
    }
    _atomic_write_json(active_manifest_path, manifest)
    return active_path


def rollback_to(runtime_dir: Path, version_id: str) -> Path:
    return activate_policy(runtime_dir, version_id, source="rollback")


def resolve_active_policy_path(runtime_dir: Path, fallback_path: Path) -> Path:
    active_path = runtime_dir / "policy_versions" / "active_policy.yaml"
    return active_path if active_path.exists() else fallback_path


def _atomic_write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    tmp_path.replace(path)


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    tmp_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_path.replace(dest)


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _next_version_id(versions_dir: Path) -> str:
    highest = 0
    for path in versions_dir.glob("policy_v*.yaml"):
        try:
            token = path.stem.replace("policy_v", "")
            highest = max(highest, int(token))
        except (ValueError, TypeError):
            continue
    return f"v{highest + 1:03d}"


def _diff_dicts(
    old: Dict[str, Any], new: Dict[str, Any], prefix: str = ""
) -> list[Dict[str, Any]]:
    changes = []
    keys = set(old.keys()) | set(new.keys())
    for key in sorted(keys):
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in old:
            changes.append({"path": path, "old": None, "new": new[key]})
            continue
        if key not in new:
            changes.append({"path": path, "old": old[key], "new": None})
            continue
        old_val = old[key]
        new_val = new[key]
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.extend(_diff_dicts(old_val, new_val, path))
        elif old_val != new_val:
            changes.append({"path": path, "old": old_val, "new": new_val})
    return changes
