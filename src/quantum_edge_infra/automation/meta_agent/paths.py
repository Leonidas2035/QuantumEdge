import os

def _resolve_base_dir() -> str:
    # Use a similar logic to meta_core.py
    base = os.path.abspath(os.path.dirname(__file__))
    curr = base
    for _ in range(5):
        if os.path.exists(os.path.join(curr, "AGENTS.md")) or os.path.exists(os.path.join(curr, "src")):
            return curr
        curr = os.path.abspath(os.path.join(curr, os.pardir))
    return base

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = _resolve_base_dir()

STAGES_PATH = os.path.join(BASE_DIR, "stages.yaml")
# Updated PROMPTS_DIR to point to docs/Documentation where .md files were moved
PROMPTS_DIR = os.path.join(REPO_ROOT, "docs", "Documentation")
PROMPTS_ARCHIVE_DIR = os.path.join(PROMPTS_DIR, "archive")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")

os.makedirs(PROMPTS_DIR, exist_ok=True)
os.makedirs(PROMPTS_ARCHIVE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
