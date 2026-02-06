import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from codex_client import CodexClient
from report_schema import REPORTS_DIR
from task_manager import create_task

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SUPERVISOR_REPORT_DIR = os.path.join(REPORTS_DIR, "supervisor")


@dataclass
class BacklogItem:
    task_type: str
    title: str
    priority: str
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def _gather_recent_summaries(limit: int = 5) -> str:
    """
    Collects recent supervisor summaries (JSON) as context for strategic planning.
    """
    if not os.path.isdir(SUPERVISOR_REPORT_DIR):
        return ""
    files = [
        os.path.join(SUPERVISOR_REPORT_DIR, f)
        for f in os.listdir(SUPERVISOR_REPORT_DIR)
        if f.endswith(".json")
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    context_parts: List[str] = []
    for path in files[:limit]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            context_parts.append(
                f"===SUPERVISOR_SUMMARY: {os.path.basename(path)}===\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            )
        except (json.JSONDecodeError, OSError):
            continue
    return "\n\n".join(context_parts)


def _llm_client() -> CodexClient:
    return CodexClient()


def generate_strategic_backlog(project: str, horizon: str = "short_term") -> Dict[str, Any]:
    """
    Uses LLM to create a strategic backlog based on recent supervisor summaries and project goals.

    Returns dict:
    {
      "backlog": [ {task_type, title, priority, description, metadata}, ... ],
      "summary": "...",
      "risks": [...],
      "raw_response": "<model text>"
    }
    """
    summaries_context = _gather_recent_summaries()
    system_prompt = (
        "You are a Systems Architect and Technical Lead for a trading bot project. "
        "You design pragmatic, incremental plans that balance risk, complexity, and impact. "
        "Supported strategic task types: strategy_review, roadmap_planning, experiment_planning, "
        "refactor_plan, infra_improvement, audit_code, risk_review, retrain_model, propose_features. "
        "Respond with a JSON object containing keys: summary (string), risks (array of strings), backlog (array). "
        "Each backlog item: task_type, title, priority (low|normal|high), description."
    )
    user_prompt = (
        f"Project: {project}\n"
        f"Horizon: {horizon}\n"
        "Context: Use recent supervisor summaries to spot recurring issues and opportunities. "
        "Focus on maintainability, correctness, risk management, and experiment velocity. "
        "Avoid over-engineering; prefer actions that can be done within 1-4 weeks.\n\n"
        "Recent supervisor summaries (if any):\n"
        f"{summaries_context or 'No summaries available.'}"
    )

    client = _llm_client()
    response = client.client.chat.completions.create(
        model=client.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=900,
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}

    backlog_items: List[BacklogItem] = []
    backlog_raw = parsed.get("backlog") if isinstance(parsed, dict) else None
    if isinstance(backlog_raw, list):
        for item in backlog_raw:
            if not isinstance(item, dict):
                continue
            task_type = item.get("task_type") or "strategy_review"
            title = item.get("title") or f"{task_type} for {project}"
            priority = item.get("priority") or "normal"
            description = item.get("description") or ""
            metadata = {k: v for k, v in item.items() if k not in {"task_type", "title", "priority", "description"}}
            backlog_items.append(
                BacklogItem(
                    task_type=task_type,
                    title=title,
                    priority=priority,
                    description=description,
                    metadata=metadata,
                )
            )

    return {
        "backlog": [item.__dict__ for item in backlog_items],
        "summary": parsed.get("summary") if isinstance(parsed, dict) else "",
        "risks": parsed.get("risks") if isinstance(parsed, dict) and isinstance(parsed.get("risks"), list) else [],
        "raw_response": content,
    }


def create_tasks_from_backlog(backlog: List[Dict[str, Any]], project: str, source: str = "strategy_agent") -> List[str]:
    """
    Materializes backlog items into Task files via task_manager.create_task.
    Returns list of created task_ids.
    """
    created_ids: List[str] = []
    for item in backlog:
        task_type = item.get("task_type") or "strategy_review"
        title = item.get("title") or f"{task_type} task"
        priority = item.get("priority") or "normal"
        description = item.get("description") or ""
        body = (
            "# Strategic Task\n"
            f"Project: {project}\n"
            f"Task Type: {task_type}\n"
            f"Priority: {priority}\n\n"
            "# Description\n"
            f"{description}\n"
        )
        task = create_task(
            project=project,
            task_type=task_type,
            title=title,
            body_markdown=body,
            priority=priority,
            source=source,
        )
        created_ids.append(task.task_id)
    return created_ids
