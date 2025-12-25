import os
from datetime import datetime
from typing import List, Optional

from task_schema import Task, TaskParseError, parse_task_file

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TASKS_DIR = os.path.join(BASE_DIR, "tasks")


def _ensure_tasks_dir() -> None:
    os.makedirs(TASKS_DIR, exist_ok=True)


def _slugify(text: str) -> str:
    """
    Simplistic slugify for IDs: lower, replace spaces with underscores, remove separators.
    """
    safe = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in text)
    safe = safe.replace(" ", "_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_").lower() or "task"


def generate_task_id(project: str, task_type: str) -> str:
    """
    Generates a readable unique task id.
    Pattern: <TYPE_PREFIX><YYYYMMDD_HHMMSS>_<project_slug>
    TYPE_PREFIX uses the first letter of task_type.
    """
    prefix = (task_type[:1] or "t").upper()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    project_slug = _slugify(project)
    return f"{prefix}{timestamp}_{project_slug}"


def task_path_from_id(task_id: str) -> str:
    """
    Returns absolute path to tasks/<task_id>.md.
    """
    _ensure_tasks_dir()
    filename = f"{task_id}.md" if not task_id.lower().endswith(".md") else task_id
    return os.path.join(TASKS_DIR, filename if filename.endswith(".md") else f"{filename}.md")


def create_task(
    project: str,
    task_type: str,
    title: str,
    body_markdown: str,
    priority: str = "normal",
    source: str = "supervisor",
    task_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Task:
    """
    Creates and writes a task file in TASKS_DIR using the canonical format.
    Returns the created Task.
    """
    _ensure_tasks_dir()
    resolved_task_id = task_id or generate_task_id(project, task_type)
    created_at_value = created_at or datetime.utcnow().isoformat() + "Z"

    header_lines = [
        f"TASK_ID: {resolved_task_id}",
        f"PROJECT: {project}",
        f"TASK_TYPE: {task_type}",
        f"TITLE: {title}",
        f"PRIORITY: {priority}",
        f"SOURCE: {source}",
        f"CREATED_AT: {created_at_value}",
        "",
    ]
    content = "\n".join(header_lines) + body_markdown.strip() + "\n"

    path = task_path_from_id(resolved_task_id)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)

    return parse_task_file(path)


def load_task(task_id_or_path: str) -> Task:
    """
    Loads a Task by task_id or direct path.
    """
    # If path exists, treat as path; otherwise resolve as task id.
    candidate_path = task_id_or_path
    if not os.path.exists(candidate_path):
        candidate_path = task_path_from_id(task_id_or_path)

    return parse_task_file(candidate_path)


def list_tasks(project: Optional[str] = None, task_type: Optional[str] = None) -> List[Task]:
    """
    Lists all tasks in TASKS_DIR, optionally filtering by project and task_type.
    """
    _ensure_tasks_dir()
    tasks: List[Task] = []
    for filename in os.listdir(TASKS_DIR):
        if not filename.endswith(".md"):
            continue
        path = os.path.join(TASKS_DIR, filename)
        try:
            task = parse_task_file(path)
        except TaskParseError:
            continue
        if project and task.project != project:
            continue
        if task_type and task.task_type != task_type:
            continue
        tasks.append(task)
    return tasks
