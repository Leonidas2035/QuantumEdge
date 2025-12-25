#!/usr/bin/env python
import os
import sys
import shutil
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
NOW = time.time()
DAY = 24 * 60 * 60
LOG_MAX_AGE_DAYS = 7

# Що точно можна чистити
DIRS_TO_REMOVE = [
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    "build",
    "dist",
]

FILE_PATTERNS_TO_REMOVE = [
    "__pycache__",      # каталоги
]

FILE_EXT_TO_REMOVE = [
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
    ".tmp",
    ".bak",
    ".old",
]

DATA_JSON_PATTERNS = [
    "_tmp.json",
    "_partial.json",
    "_error.json",
]

SAFE_LOG_DIRS = [
    "logs",
]

PROTECTED_DIRS = {
    "config",
    "bot",
    "data",
    "models",
    ".git",
    "run_configs",
}


def is_under_protected(path: Path) -> bool:
    for part in path.relative_to(PROJECT_ROOT).parts:
        if part in PROTECTED_DIRS:
            return True
    return False


def remove_dir(path: Path, dry: bool):
    if dry:
        print(f"[DRY] rm -rf {path}")
    else:
        shutil.rmtree(path, ignore_errors=True)
        print(f"[DEL] dir {path}")


def remove_file(path: Path, dry: bool):
    if dry:
        print(f"[DRY] rm {path}")
    else:
        try:
            path.unlink()
            print(f"[DEL] file {path}")
        except FileNotFoundError:
            pass


def clean_venv(dry: bool):
    """
    Очищує вміст папки venv/, але саму папку venv не видаляє.
    """
    venv_dir = PROJECT_ROOT / "venv"
    if not venv_dir.exists() or not venv_dir.is_dir():
        return

    print("\n[INFO] Cleaning venv contents (keeping venv folder)...")

    for p in venv_dir.iterdir():
        if p.is_dir():
            remove_dir(p, dry)
        elif p.is_file():
            remove_file(p, dry)


def clean_logs(dry: bool):
    for log_root in SAFE_LOG_DIRS:
        root = PROJECT_ROOT / log_root
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in (".log", ".jsonl"):
                continue
            age_days = (NOW - p.stat().st_mtime) / DAY
            if age_days > LOG_MAX_AGE_DAYS:
                remove_file(p, dry)


def main():
    dry = "--apply" not in sys.argv
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Mode: {'DRY-RUN' if dry else 'APPLY'}")

    # Видалити службові каталоги/кеші
    for name in DIRS_TO_REMOVE:
        for p in PROJECT_ROOT.rglob(name):
            if p.is_dir() and not is_under_protected(p):
                remove_dir(p, dry)

    # Видалити __pycache__
    for p in PROJECT_ROOT.rglob("__pycache__"):
        if p.is_dir():
            remove_dir(p, dry)

    # Видалити файли по розширенню
    for ext in FILE_EXT_TO_REMOVE:
        for p in PROJECT_ROOT.rglob(f"*{ext}"):
            if p.is_file() and not is_under_protected(p):
                remove_file(p, dry)

    # Тимчасові json у data
    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        for pattern in DATA_JSON_PATTERNS:
            for p in data_dir.rglob(f"*{pattern}"):
                if p.is_file():
                    remove_file(p, dry)

        # Повністю видалити data/tmp
        tmp_dir = data_dir / "tmp"
        if tmp_dir.exists():
            remove_dir(tmp_dir, dry)

    # Старі логи
    clean_logs(dry)

    # Очистити вміст venv, але не видаляти саму папку
    clean_venv(dry)

    print("Cleanup finished.")


if __name__ == "__main__":
    main()