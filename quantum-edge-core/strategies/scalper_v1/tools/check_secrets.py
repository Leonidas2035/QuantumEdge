"""
Lightweight secret guard for CI/local use.

Usage:
    python tools/check_secrets.py

Exits non-zero if suspicious secrets are detected.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "logs", "data", "storage"}
SUSPECT_FILE_TOKENS = ("secret", "secrets", ".env", "apikey", "api-key")

# Common key patterns (best-effort heuristic)
PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),  # OpenAI-style keys
    re.compile(r"(?i)binance[_-]?api[_-]?key"),
    re.compile(r"(?i)openai[_-]?api[_-]?key"),
    re.compile(r"(?i)api[_-]?secret"),
]


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def main() -> int:
    findings: list[str] = []

    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        if _should_skip(path):
            continue

        name_lower = path.name.lower()
        if any(token in name_lower for token in SUSPECT_FILE_TOKENS):
            findings.append(f"suspect filename: {path}")

        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        for pat in PATTERNS:
            if pat.search(text):
                findings.append(f"pattern '{pat.pattern}' in {path}")
                break

    if findings:
        print("[ERROR] Potential secrets detected:")
        for item in findings:
            print(f" - {item}")
        return 1

    print("[OK] No obvious secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
