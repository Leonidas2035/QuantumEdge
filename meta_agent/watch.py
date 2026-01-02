from typing import Optional

from inbox_processor import process_inbox_once as _process_inbox_once
from meta_core import run_task


def process_inbox_once(
    inbox: str,
    archive: str,
    failed: str,
    logger,
    timeout_seconds: Optional[int] = None,
    llm_timeout_seconds: Optional[int] = None,
    retries: int = 0,
) -> dict:
    return _process_inbox_once(
        inbox=inbox,
        archive=archive,
        failed=failed,
        logger=logger,
        timeout_seconds=timeout_seconds,
        llm_timeout_seconds=llm_timeout_seconds,
        retries=retries,
        run_task_func=globals().get("run_task"),
    )
