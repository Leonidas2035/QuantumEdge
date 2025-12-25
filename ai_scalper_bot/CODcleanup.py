"""
Safe, configurable cleanup utility for the ai_scalper_bot repo.

Defaults to dry-run (no deletions). Use --apply to actually delete.

Targets:
 - Python caches: __pycache__, *.pyc, *.pyo, *.pyd
 - Common tool caches: .pytest_cache, .mypy_cache, .ruff_cache, .ipynb_checkpoints
 - Build/installer artifacts: build/, dist/, installer/tmp/
 - Editor/OS junk: .DS_Store, Thumbs.db
 - Temp/intermediate files: *.tmp, *.bak, *.old, *_tmp.json, *_partial.json, *_error.json
 - Logs older than N days (default 7) in logs/ or data/logs/: *.log, *.jsonl

Protections:
 - Never touches .git
 - Never deletes CODcleanup.py
 - Does not match code/config/model files (pkl/joblib/onnx/yaml/py)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Set, Tuple


ROOT = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).resolve()

# Directories to prune entirely when matched by name
DIR_DELETE_NAMES: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    "build",
    "dist",
}

# File patterns that are safe to delete
FILE_PATTERNS: Tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.tmp",
    "*.bak",
    "*.old",
    "*_tmp.json",
    "*_partial.json",
    "*_error.json",
    ".DS_Store",
    "Thumbs.db",
)

# Paths to skip recursion into
SKIP_DIR_NAMES: Set[str] = {".git", "venv", ".venv"}

# Additional directories to drop when specifically under certain parents
SPECIAL_TMP_DIRS: Tuple[Tuple[str, str], ...] = (
    ("installer", "tmp"),  # installer/tmp
    ("data", "tmp"),       # data/tmp
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe cleanup for ai_scalper_bot (dry-run by default).")
    parser.add_argument("--apply", action="store_true", help="Actually delete files/directories.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (default).")
    parser.add_argument(
        "--max-log-age",
        type=int,
        default=7,
        help="Delete logs older than N days in logs/ or data/logs/. Default: 7.",
    )
    args = parser.parse_args()
    # default to dry-run unless apply explicitly set
    if args.apply:
        args.dry_run = False
    else:
        args.dry_run = True
    return args


def human_mb(bytes_count: int) -> float:
    return round(bytes_count / (1024 * 1024), 2)


def safe_stat(path: Path):
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def dir_should_be_deleted(path: Path) -> bool:
    if path.name in DIR_DELETE_NAMES:
        return True
    for parent_name, child_name in SPECIAL_TMP_DIRS:
        if path.name == child_name and path.parent.name == parent_name:
            return True
    # signal_model/tmp or similar
    if path.name == "tmp" and "signal_model" in path.parts:
        return True
    return False


def file_matches_patterns(path: Path, patterns: Iterable[str]) -> bool:
    name = path.name
    for pat in patterns:
        if path.match(pat):
            return True
        # pathlib.match is relative; ensure filename-only match too
        if Path(name).match(pat):
            return True
    return False


def collect_dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            st = safe_stat(p)
            if st:
                total += st.st_size
    return total


def scan(root: Path, log_age_days: int) -> Tuple[List[Tuple[Path, str]], List[Tuple[Path, str]]]:
    files_to_delete: List[Tuple[Path, str]] = []
    dirs_to_delete: List[Tuple[Path, str]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        # prune recursion into skipped directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]

        # schedule directories
        for d in list(dirnames):
            candidate = current / d
            if dir_should_be_deleted(candidate):
                dirs_to_delete.append((candidate, "cache/tmp dir"))
                # avoid descending into a dir we plan to remove
                dirnames.remove(d)

        # schedule files
        for fname in filenames:
            fpath = current / fname
            if fpath.resolve() == THIS_FILE:
                continue
            if file_matches_patterns(fpath, FILE_PATTERNS):
                files_to_delete.append((fpath, "cache/temp file"))

    # Logs older than threshold
    cutoff = datetime.now() - timedelta(days=log_age_days)
    log_roots = [root / "logs", root / "data" / "logs"]
    for log_root in log_roots:
        if not log_root.exists():
            continue
        for log_file in log_root.rglob("*"):
            if not log_file.is_file():
                continue
            if not file_matches_patterns(log_file, ("*.log", "*.jsonl")):
                continue
            st = safe_stat(log_file)
            if not st:
                continue
            if datetime.fromtimestamp(st.st_mtime) < cutoff:
                files_to_delete.append((log_file, f"log>{log_age_days}d"))

    return files_to_delete, dirs_to_delete


def delete_path(path: Path, is_dir: bool, dry_run: bool) -> int:
    """
    Delete a path; return freed bytes (approx). In dry-run, return size only.
    """
    size = collect_dir_size(path) if is_dir else (safe_stat(path).st_size if safe_stat(path) else 0)
    if dry_run:
        return size
    try:
        if is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return 0
    except Exception as exc:  # pragma: no cover - best-effort logging
        print(f"[WARN] Failed to delete {path}: {exc}")
        return 0
    return size


def main():
    args = parse_args()
    files_to_delete, dirs_to_delete = scan(ROOT, args.max_log_age)

    total_files = 0
    total_dirs = 0
    freed_bytes = 0

    for dpath, reason in dirs_to_delete:
        freed_bytes += delete_path(dpath, is_dir=True, dry_run=args.dry_run)
        total_dirs += 1
        print(f"{'[DRY-RUN]' if args.dry_run else '[DELETE]'} DIR  {dpath} ({reason})")

    for fpath, reason in files_to_delete:
        freed_bytes += delete_path(fpath, is_dir=False, dry_run=args.dry_run)
        total_files += 1
        print(f"{'[DRY-RUN]' if args.dry_run else '[DELETE]'} FILE {fpath} ({reason})")

    print("\nSummary:")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print(f"  Files: {total_files} {'to delete' if args.dry_run else 'deleted'}")
    print(f"  Dirs:  {total_dirs} {'to delete' if args.dry_run else 'deleted'}")
    print(f"  Est. space freed: {human_mb(freed_bytes)} MB")


if __name__ == "__main__":
    sys.exit(main())
