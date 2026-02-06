import os
from dataclasses import dataclass
from typing import Optional


class TaskParseError(Exception):
    """Raised when a task file is malformed or missing required fields."""


@dataclass
class Task:
    task_id: str
    project: str
    task_type: str
    title: str
    priority: str
    source: str
    created_at: Optional[str]
    raw_header: str
    body_markdown: str
    path: str


REQUIRED_FIELDS = {"TASK_ID", "PROJECT", "TASK_TYPE", "TITLE"}


def _split_header_body(content: str) -> tuple[list[str], str]:
    """
    Splits raw file content into header lines and body text.
    Header ends at the first blank line.
    """
    lines = content.splitlines()
    header_lines: list[str] = []
    body_start = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == "":
            body_start = idx + 1
            break
        header_lines.append(line.rstrip("\n"))
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return header_lines, body


def _parse_header_lines(header_lines: list[str]) -> dict:
    """
    Parses KEY: VALUE lines into a dict, ignoring comment lines starting with '#'.
    Allows spaces around ':'.
    """
    header = {}
    for line in header_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise TaskParseError(f"Invalid header line (missing ':'): {line}")
        key, value = stripped.split(":", 1)
        header[key.strip().upper()] = value.strip()
    return header


def parse_task_file(path: str) -> Task:
    """
    Parses a task file in canonical KEY: VALUE + markdown body format.

    Steps:
      1) Read file.
      2) Split into header and body by the first blank line.
      3) Parse KEY: VALUE pairs into a dict (case-insensitive keys).
      4) Validate required fields (TASK_ID, PROJECT, TASK_TYPE, TITLE).
      5) Return Task dataclass instance.
    """
    if not os.path.exists(path):
        raise TaskParseError(f"Task file not found: {path}")

    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()

    header_lines, body = _split_header_body(content)
    header_dict = _parse_header_lines(header_lines)

    missing = [field for field in REQUIRED_FIELDS if field not in header_dict]
    if missing:
        raise TaskParseError(f"Missing required fields: {', '.join(missing)}")

    task = Task(
        task_id=header_dict["TASK_ID"],
        project=header_dict["PROJECT"],
        task_type=header_dict["TASK_TYPE"],
        title=header_dict["TITLE"],
        priority=header_dict.get("PRIORITY", "normal"),
        source=header_dict.get("SOURCE", "supervisor"),
        created_at=header_dict.get("CREATED_AT"),
        raw_header="\n".join(header_lines).strip(),
        body_markdown=body.strip(),
        path=os.path.abspath(path),
    )
    return task
