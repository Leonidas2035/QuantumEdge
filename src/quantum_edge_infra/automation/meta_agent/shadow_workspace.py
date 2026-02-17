import fnmatch
import os
import shutil
import subprocess
from typing import Iterable, Optional

from task_contract import DEFAULT_DENY_GLOBS

DEFAULT_IGNORE = DEFAULT_DENY_GLOBS + [
    ".git",
    "**/.git/**",
    "node_modules",
    "**/node_modules/**",
]


def _should_ignore(rel_path: str, ignore_globs: Iterable[str]) -> bool:
    rel = rel_path.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pattern) for pattern in ignore_globs)


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _copy_tree(src: str, dest: str, ignore_globs: Iterable[str]) -> tuple[int, int]:
    files = 0
    total_bytes = 0
    for root, dirs, filenames in os.walk(src):
        rel_root = os.path.relpath(root, src)
        if rel_root == ".":
            rel_root = ""
        if rel_root and _should_ignore(rel_root, ignore_globs):
            dirs[:] = []
            continue
        filtered_dirs = []
        for d in dirs:
            rel_dir = os.path.join(rel_root, d) if rel_root else d
            if _should_ignore(rel_dir, ignore_globs):
                continue
            filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        for fname in filenames:
            rel_file = os.path.join(rel_root, fname) if rel_root else fname
            if _should_ignore(rel_file, ignore_globs):
                continue
            src_path = os.path.join(root, fname)
            dest_path = os.path.join(dest, rel_file)
            _safe_mkdir(os.path.dirname(dest_path))
            shutil.copy2(src_path, dest_path)
            files += 1
            try:
                total_bytes += os.path.getsize(dest_path)
            except OSError:
                pass
    return files, total_bytes


def _git_available(project_root: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except Exception:
        return False


def _create_git_worktree(project_root: str, shadow_dir: str, logger) -> bool:
    try:
        if os.path.exists(shadow_dir):
            shutil.rmtree(shadow_dir, ignore_errors=True)
        proc = subprocess.run(
            [
                "git",
                "-C",
                project_root,
                "worktree",
                "add",
                "--detach",
                shadow_dir,
                "HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.info(
                "git worktree failed: %s", proc.stderr.strip() or proc.stdout.strip()
            )
            return False
        return True
    except Exception as exc:
        logger.info("git worktree exception: %s", exc)
        return False


def create_shadow(
    project_root: str,
    run_dir: str,
    strategy: str,
    logger,
    ignore_globs: Optional[Iterable[str]] = None,
) -> str:
    shadow_base = os.path.join(run_dir, "shadow")
    _safe_mkdir(shadow_base)
    ignore = list(ignore_globs or DEFAULT_IGNORE)

    project_name = os.path.basename(project_root.rstrip("\\/")) or "project"
    shadow_dir = os.path.join(shadow_base, project_name)
    used_strategy = strategy

    if strategy == "git_worktree" and _git_available(project_root):
        shadow_dir = os.path.join(shadow_base, "worktree")
        if not _create_git_worktree(project_root, shadow_dir, logger):
            used_strategy = "copy"

    if used_strategy == "copy":
        if os.path.exists(shadow_dir):
            shutil.rmtree(shadow_dir, ignore_errors=True)
        _safe_mkdir(shadow_dir)
        files, total_bytes = _copy_tree(project_root, shadow_dir, ignore)
        logger.info(
            "Shadow copy created: %s files=%s bytes=%s", shadow_dir, files, total_bytes
        )
    else:
        logger.info("Shadow worktree created: %s", shadow_dir)

    info_path = os.path.join(shadow_base, "shadow_info.json")
    try:
        shadow_dir_safe = shadow_dir.replace("\\", "/")
        with open(info_path, "w", encoding="utf-8") as handle:
            handle.write(
                f'{{"shadow_dir": "{shadow_dir_safe}", "strategy": "{used_strategy}"}}'
            )
    except Exception:
        pass

    return shadow_dir


def cleanup_shadow(
    shadow_dir: str,
    keep: bool,
    strategy: Optional[str],
    project_root: Optional[str],
    logger,
) -> None:
    if keep or not shadow_dir:
        return
    if strategy == "git_worktree" and project_root:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    project_root,
                    "worktree",
                    "remove",
                    "--force",
                    shadow_dir,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            return
        except Exception:
            pass
    try:
        shutil.rmtree(shadow_dir, ignore_errors=True)
        logger.info("Shadow cleaned: %s", shadow_dir)
    except Exception:
        pass
