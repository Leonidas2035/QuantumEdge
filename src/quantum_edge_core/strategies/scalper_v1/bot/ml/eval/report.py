"""Shared report rendering helpers."""

from __future__ import annotations

from typing import Dict


def render_section(title: str, body: Dict[str, object]) -> str:
    lines = [f"## {title}", ""]
    for key, value in body.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)
