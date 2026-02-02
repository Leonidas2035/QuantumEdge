import os
from dataclasses import dataclass
from typing import List, Optional

import yaml

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback
    get_qe_paths = None


DEFAULT_PROJECTS_PATH = os.path.join("config", "projects.yaml")


@dataclass
class ProjectEntry:
    project_id: str
    root: str
    label: str
    default_include_globs: List[str]
    deny_globs: List[str]


def _resolve_base_dir() -> str:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    if get_qe_paths:
        try:
            return str(get_qe_paths()["qe_root"])
        except Exception:
            pass
    base = os.path.abspath(os.path.dirname(__file__))
    parent = os.path.abspath(os.path.join(base, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(os.path.join(parent, "ai_scalper_bot")):
        return parent
    return base


def _resolve_projects_path(path: Optional[str]) -> str:
    base = _resolve_base_dir()
    candidate = path or DEFAULT_PROJECTS_PATH
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def load_projects_registry(path: Optional[str] = None) -> List[ProjectEntry]:
    resolved = _resolve_projects_path(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"projects.yaml not found: {resolved}")
    with open(resolved, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    projects_raw = raw.get("projects") if isinstance(raw, dict) else raw
    if not isinstance(projects_raw, list):
        raise ValueError("projects.yaml must contain a 'projects' list")
    entries: List[ProjectEntry] = []
    for item in projects_raw:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "")
        root = str(item.get("root") or "")
        label = str(item.get("label") or project_id)
        include_globs = list(item.get("default_include_globs") or [])
        deny_globs = list(item.get("deny_globs") or [])
        if not project_id or not root:
            continue
        entries.append(
            ProjectEntry(
                project_id=project_id,
                root=root,
                label=label,
                default_include_globs=include_globs,
                deny_globs=deny_globs,
            )
        )
    if not entries:
        raise ValueError("projects.yaml has no valid project entries")
    return entries
