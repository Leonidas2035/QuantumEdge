import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

STAGES_PATH = os.path.join(BASE_DIR, "stages.yaml")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
PROMPTS_ARCHIVE_DIR = os.path.join(PROMPTS_DIR, "archive")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")

os.makedirs(PROMPTS_DIR, exist_ok=True)
os.makedirs(PROMPTS_ARCHIVE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
