import os
import re
import shutil
from typing import Dict, List, Optional

import yaml


def extract_job_name_from_path(path: str) -> Optional[str]:
    """
    If basename matches S<number><job_name>.md, return job_name; else None.
    """
    basename = os.path.basename(path)
    match = re.match(r"^S\d+(.+)\.md$", basename)
    if match:
        job_name = match.group(1).strip()
        return job_name or None
    return None


def _job_name_from_target_project(target_project: Optional[str]) -> str:
    if not target_project:
        return "default"
    base = os.path.basename(os.path.abspath(target_project))
    return base or "default"


def _resolve_task_path(task_path: str, prompts_root: str) -> Optional[str]:
    """
    Resolve the provided path, trying prompts_root as a fallback.
    Returns an absolute path or None if not found.
    """
    candidate = os.path.abspath(task_path)
    if os.path.exists(candidate):
        return candidate

    fallback = os.path.abspath(os.path.join(prompts_root, task_path))
    if os.path.exists(fallback):
        return fallback

    return None


def _unique_destination(directory: str, filename: str) -> str:
    name, ext = os.path.splitext(filename)
    dest = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(directory, f"{name}({counter}){ext}")
        counter += 1
    return dest


def _relpath(path: str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def archive_task_file(
    task_path: str,
    target_project: Optional[str] = None,
    prompts_root: str = "prompts",
    archive_root: str = "prompts/archive",
) -> None:
    """
    Archive a single task file into prompts/archive/<job_name>/.
    """
    resolved = _resolve_task_path(task_path, prompts_root)
    if not resolved:
        raise FileNotFoundError(f"Task file not found for archiving: {task_path}")

    job_name = extract_job_name_from_path(resolved) or _job_name_from_target_project(target_project)
    archive_dir = os.path.abspath(os.path.join(archive_root, job_name))
    os.makedirs(archive_dir, exist_ok=True)

    destination = _unique_destination(archive_dir, os.path.basename(resolved))
    shutil.move(resolved, destination)
    print(f"[INFO] Archived prompt {_relpath(resolved)} -> {_relpath(destination)}")


def archive_stage_prompts(
    stages: List[Dict],
    target_project: Optional[str] = None,
    stages_path: str = "stages.yaml",
    prompts_root: str = "prompts",
    archive_root: str = "prompts/archive",
) -> None:
    """
    Archive all prompt files referenced in stages, then clear stages.yaml.
    """
    if not stages:
        try:
            with open(stages_path, "w", encoding="utf-8") as handle:
                handle.write("[]\n")
            print(f"[INFO] No stages to archive; cleared {_relpath(stages_path)}.")
        except OSError as exc:
            print(f"[WARN] Failed to clear {stages_path}: {exc}")
        return

    resolved_prompts: List[str] = []
    for stage in stages:
        prompt_path = stage.get("prompt")
        if not prompt_path:
            print(f"[WARN] Stage entry missing 'prompt': {stage}")
            continue

        resolved = _resolve_task_path(prompt_path, prompts_root)
        if not resolved:
            raise FileNotFoundError(f"Prompt file not found for stage '{stage.get('name', 'unnamed')}': {prompt_path}")

        resolved_prompts.append(resolved)

    job_name = _job_name_from_target_project(target_project)
    for path in resolved_prompts:
        matched = extract_job_name_from_path(path)
        if matched:
            job_name = matched
            break

    archive_dir = os.path.abspath(os.path.join(archive_root, job_name))
    os.makedirs(archive_dir, exist_ok=True)

    for prompt_path in resolved_prompts:
        destination = _unique_destination(archive_dir, os.path.basename(prompt_path))
        shutil.move(prompt_path, destination)
        print(f"[INFO] Archived prompt {_relpath(prompt_path)} -> {_relpath(destination)}")

    try:
        with open(stages_path, "w", encoding="utf-8") as handle:
            handle.write("[]\n")
        print(f"[INFO] Cleared stages file at {_relpath(stages_path)}")
    except OSError as exc:
        print(f"[WARN] Failed to clear {stages_path}: {exc}")


def archive_completed_tasks(
    stages: Optional[List[Dict]] = None,
    target_project: Optional[str] = None,
    stages_path: str = "stages.yaml",
    prompts_root: str = "prompts",
    archive_root: str = "prompts/archive",
) -> None:
    """
    Backward-compatible wrapper. If stages is None, load from stages.yaml.
    """
    stage_data: List[Dict] = stages if stages is not None else []
    if stages is None and os.path.exists(stages_path):
        try:
            with open(stages_path, "r", encoding="utf-8") as handle:
                stage_data = yaml.safe_load(handle) or []
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Failed to parse {stages_path}: {exc}") from exc

    archive_stage_prompts(
        stage_data,
        target_project=target_project,
        stages_path=stages_path,
        prompts_root=prompts_root,
        archive_root=archive_root,
    )
