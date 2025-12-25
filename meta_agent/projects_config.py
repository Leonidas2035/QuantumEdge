import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml

from paths import BASE_DIR

DEFAULT_PROJECTS_PATH = os.path.join(BASE_DIR, "config", "projects.yaml")


@dataclass
class ProjectInfo:
    project_id: str
    root_path: Path  # absolute path
    description: str = ""


@dataclass
class ProjectRegistry:
    default_project_id: str
    projects: Dict[str, ProjectInfo]

    def get(self, project_id: Optional[str]) -> Optional[ProjectInfo]:
        if not project_id:
            return self.projects.get(self.default_project_id)
        return self.projects.get(project_id)


def _ensure_default_config(path: str = DEFAULT_PROJECTS_PATH) -> None:
    """
    Writes a simple default config if none exists, to keep the system usable out of the box.
    """
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    default_yaml = {
        "default": "ai_scalper_bot",
        "projects": {
            "ai_scalper_bot": {
                "path": "C:/QuantumEdge/ai_scalper_bot",
                "description": "QuantumEdge trading engine",
            },
            "supervisor_agent": {
                "path": "C:/QuantumEdge/SupervisorAgent",
                "description": "Supervisor control plane",
            },
            "meta_agent": {
                "path": "C:/QuantumEdge/meta_agent",
                "description": "Meta-Agent orchestrator",
            },
        },
    }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(default_yaml, handle, allow_unicode=True, sort_keys=False)


def _normalize_legacy(raw: dict) -> dict:
    """
    Accept legacy shapes and return normalized dict with default/projects keys.
    """
    if not raw:
        return {}
    if "projects" in raw:
        return raw
    projects_block = {}
    for pid, path in raw.items():
        if pid in {"default", "projects"}:
            continue
        projects_block[pid] = {"path": path}
    return {"default": raw.get("default", "ai_scalper_bot"), "projects": projects_block}


def load_project_registry(config_path: str = DEFAULT_PROJECTS_PATH) -> ProjectRegistry:
    """
    Reads config/projects.yaml, resolves paths, and returns a registry.
    If the file is missing, a default config is created automatically.
    """
    _ensure_default_config(config_path)

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse project registry: {exc}")
    except OSError as exc:
        raise RuntimeError(f"Failed to read project registry: {exc}")

    normalized = _normalize_legacy(raw)
    default_project_id = normalized.get("default") or "ai_scalper_bot"
    projects_map = normalized.get("projects") or {}
    projects: Dict[str, ProjectInfo] = {}

    for pid, info in projects_map.items():
        if not isinstance(info, dict):
            continue
        rel_path = info.get("path")
        if not rel_path:
            continue
        root_path = Path(rel_path)
        if not root_path.is_absolute():
            root_path = Path(BASE_DIR) / rel_path
        projects[pid] = ProjectInfo(
            project_id=pid,
            root_path=root_path.resolve(),
            description=str(info.get("description") or ""),
        )

    if default_project_id not in projects and projects:
        default_project_id = next(iter(projects.keys()))

    if not projects:
        raise RuntimeError("No projects defined in config/projects.yaml")

    return ProjectRegistry(default_project_id=default_project_id, projects=projects)


def resolve_project_root(project_id: Optional[str], registry: ProjectRegistry) -> ProjectInfo:
    """
    Resolves a project_id to ProjectInfo, using default when None. Raises on unknown id.
    """
    info = registry.get(project_id)
    if not info:
        raise KeyError(f"Unknown project id '{project_id}'. Available: {', '.join(registry.projects.keys())}")
    return info


def get_default_project_id(registry: ProjectRegistry) -> str:
    return registry.default_project_id


def get_project_path(project_id: str, registry: ProjectRegistry) -> Path:
    return resolve_project_root(project_id, registry).root_path


def list_projects(registry: ProjectRegistry) -> Dict[str, ProjectInfo]:
    return registry.projects
