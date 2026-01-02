import json
import os
import shutil
from datetime import datetime, timezone
from typing import Callable, Optional

from meta_core import run_task


def _timestamp_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_failure_reason(dest_dir: str, basename: str, report) -> None:
    payload = {
        "run_id": report.run_id,
        "task_id": report.task_id,
        "verdict": report.verdict,
        "exit_code": report.exit_code,
        "errors": report.errors,
    }
    path = os.path.join(dest_dir, f"{basename}.error.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _resolve_tasks(inbox: str) -> list[str]:
    tasks = []
    for entry in os.listdir(inbox):
        if entry.lower() in {"stop", "pause"}:
            continue
        if entry.lower().endswith((".yaml", ".yml", ".md", ".markdown")):
            tasks.append(os.path.join(inbox, entry))
    tasks.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)))
    return tasks


def process_inbox_once(
    inbox: str,
    archive: str,
    failed: str,
    logger,
    timeout_seconds: Optional[int] = None,
    llm_timeout_seconds: Optional[int] = None,
    retries: int = 0,
    run_task_func: Optional[Callable[..., object]] = None,
) -> dict:
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(archive, exist_ok=True)
    os.makedirs(failed, exist_ok=True)

    if os.path.exists(os.path.join(inbox, "STOP")):
        return {"stop": True, "processed": 0, "reports": []}
    if os.path.exists(os.path.join(inbox, "PAUSE")):
        return {"pause": True, "processed": 0, "reports": []}

    runner = run_task_func or run_task
    processed = 0
    reports = []
    for task_path in _resolve_tasks(inbox):
        basename = os.path.basename(task_path)
        cli_context = {
            "command": "watch",
            "args_sanitized": [basename],
        }

        report = runner(
            task_path,
            timeout_seconds=timeout_seconds,
            llm_timeout_seconds=llm_timeout_seconds,
            cli_context=cli_context,
        )

        if report.exit_code == 50:
            logger.info("Lock busy; deferring inbox processing.")
            return {"lock_busy": True, "processed": processed, "reports": reports}

        dest_prefix = f"{report.run_id}_{basename}"
        if report.exit_code in {0, 10, 11, 12, 20}:
            dest_path = os.path.join(archive, dest_prefix)
            shutil.move(task_path, dest_path)
            logger.info("Task archived: %s", dest_prefix)
        else:
            dest_path = os.path.join(failed, dest_prefix)
            shutil.move(task_path, dest_path)
            _write_failure_reason(failed, dest_prefix, report)
            logger.info("Task failed: %s", dest_prefix)

        reports.append(
            {
                "task_path": task_path,
                "task_name": basename,
                "report": report,
            }
        )
        processed += 1

    return {"processed": processed, "reports": reports}
