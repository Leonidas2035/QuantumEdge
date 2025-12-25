import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

# Default settings for context collection
DEFAULT_INCLUDE_EXTS: Set[str] = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".txt"}
DEFAULT_EXCLUDE_DIRS: Set[str] = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "data",
    "logs",
    "output",
    "reports",
    "tmp",
    "temp",
    "coverage",
    "htmlcov",
}


@dataclass
class ScannerStats:
    """
    Lightweight stats about collected context to help with logging and debugging.
    """

    files_included: int = 0
    chars_collected: int = 0
    stopped_due_to_limit: bool = False
    skipped_large_files: List[str] = field(default_factory=list)


class ProjectScanner:
    """
    Collects a textual snapshot of a project, enforcing size limits and skipping noisy dirs.
    """

    def __init__(
        self,
        project_root: str,
        include_exts: Optional[Iterable[str]] = None,
        exclude_dirs: Optional[Iterable[str]] = None,
        max_file_chars: int = 100_000,
    ):
        self.project_root = os.path.abspath(project_root)
        self.include_exts = {ext.lower() for ext in (include_exts or DEFAULT_INCLUDE_EXTS)}
        self.exclude_dirs = {d.lower() for d in (exclude_dirs or DEFAULT_EXCLUDE_DIRS)}
        self.max_file_chars = max_file_chars
        self.stats = ScannerStats()

    def _should_exclude_dir(self, dirname: str) -> bool:
        return dirname.lower() in self.exclude_dirs

    def _should_include_file(self, filename: str) -> bool:
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.include_exts

    def collect_project_context(self, max_chars: int = 250_000) -> str:
        """
        Walks the project tree and returns a concatenated string of file contents
        limited to `max_chars`. Large files (> max_file_chars) are skipped.
        Directory exclusions and extension filters are applied to reduce noise.
        """
        context_parts: List[str] = []
        total_chars = 0

        for root, dirs, files in os.walk(self.project_root):
            # Prune excluded directories in-place for performance
            dirs[:] = [d for d in dirs if not self._should_exclude_dir(d)]

            for fname in sorted(files):
                if not self._should_include_file(fname):
                    continue

                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.project_root)

                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                        content = handle.read()
                except OSError:
                    continue

                if len(content) > self.max_file_chars:
                    self.stats.skipped_large_files.append(rel_path)
                    continue

                header = f"### FILE: {rel_path}\n"
                snippet = header + content.strip() + "\n\n"

                if total_chars + len(snippet) > max_chars:
                    self.stats.stopped_due_to_limit = True
                    # Stop collecting further to respect the limit.
                    context_parts.append(snippet[: max(0, max_chars - total_chars)])
                    total_chars = max_chars
                    break

                context_parts.append(snippet)
                total_chars += len(snippet)
                self.stats.files_included += 1

            if total_chars >= max_chars:
                break

        self.stats.chars_collected = total_chars
        return "".join(context_parts)

    def collect_project_files(self, max_chars: int = 250_000) -> str:
        """
        Backward-compatible alias for collect_project_context.
        """
        return self.collect_project_context(max_chars=max_chars)


def collect_project_context(
    project_root: str,
    max_chars: int = 250_000,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
) -> str:
    """
    Functional wrapper to collect project context without instantiating the class directly.
    """
    scanner = ProjectScanner(project_root, include_exts=include_patterns, exclude_dirs=exclude_dirs)
    return scanner.collect_project_context(max_chars=max_chars)
