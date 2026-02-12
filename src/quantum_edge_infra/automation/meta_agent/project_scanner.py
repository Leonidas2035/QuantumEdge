import fnmatch
import glob
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

from secret_masking import mask_secrets

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
    "runtime",
    "patches",
    "tmp",
    "temp",
    "coverage",
    "htmlcov",
}

DEFAULT_EXCLUDE_FILES: Set[str] = {
    ".env",
    ".env.*",
    "*.env",
    "secrets*",
    "*.key",
    "*.pem",
    "*.pfx",
    "*.p12",
    "*.enc",
    "*.kdbx",
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
    included_files: List[str] = field(default_factory=list)


class ProjectScanner:
    """
    Collects a textual snapshot of a project, enforcing size limits and skipping noisy dirs.
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        include_exts: Optional[Iterable[str]] = None,
        exclude_dirs: Optional[Iterable[str]] = None,
        exclude_files: Optional[Iterable[str]] = None,
        max_file_chars: int = 100_000,
    ):
        if project_root is None:
            # Default to repo root by looking for src/quantum_edge_core
            curr = Path(__file__).resolve()
            found_root = None
            for _ in range(6):
                if (curr / "src" / "quantum_edge_core").is_dir():
                    found_root = str(curr)
                    break
                curr = curr.parent
            self.project_root = found_root or os.path.abspath(".")
        else:
            self.project_root = os.path.abspath(project_root)
        self.include_exts = {ext.lower() for ext in (include_exts or DEFAULT_INCLUDE_EXTS)}
        self.exclude_dirs = {d.lower() for d in (exclude_dirs or DEFAULT_EXCLUDE_DIRS)}
        self.exclude_files = {f.lower() for f in (exclude_files or DEFAULT_EXCLUDE_FILES)}
        self.max_file_chars = max_file_chars
        self.stats = ScannerStats()

    def _should_exclude_dir(self, dirname: str) -> bool:
        return dirname.lower() in self.exclude_dirs

    def _should_include_file(self, filename: str) -> bool:
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.include_exts

    def _should_exclude_file(self, rel_path: str, filename: str) -> bool:
        rel_lower = rel_path.replace("\\", "/").lower()
        name_lower = filename.lower()
        for pattern in self.exclude_files:
            if fnmatch.fnmatch(name_lower, pattern) or fnmatch.fnmatch(rel_lower, pattern):
                return True
        return False

    def _is_denied(self, rel_path: str, deny_globs: Optional[Iterable[str]]) -> bool:
        if not deny_globs:
            return False
        rel_lower = rel_path.replace("\\", "/")
        return any(fnmatch.fnmatch(rel_lower, pat) for pat in deny_globs)

    def _in_excluded_dir(self, rel_path: str) -> bool:
        parts = rel_path.replace("\\", "/").split("/")
        return any(part.lower() in self.exclude_dirs for part in parts)

    def _get_git_files(self) -> Optional[List[str]]:
        try:
            # Use git ls-files to get tracked and untracked (but not ignored) files
            proc = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True,
            )
            return [os.path.join(self.project_root, line) for line in proc.stdout.splitlines() if line]
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def collect_project_context(
        self,
        max_chars: int = 250_000,
        include_globs: Optional[Iterable[str]] = None,
        focus_files: Optional[Iterable[str]] = None,
        deny_globs: Optional[Iterable[str]] = None,
    ) -> str:
        """
        Walks the project tree and returns a concatenated string of file contents
        limited to `max_chars`. Large files (> max_file_chars) are skipped.
        Directory exclusions and extension filters are applied to reduce noise.
        Respects .gitignore by using git ls-files when available.
        """
        context_parts: List[str] = []
        total_chars = 0

        explicit_files: List[str] = []
        if focus_files:
            for entry in focus_files:
                abs_path = entry if os.path.isabs(entry) else os.path.join(self.project_root, entry)
                abs_path = os.path.abspath(abs_path)
                try:
                    common = os.path.commonpath([abs_path, self.project_root])
                except ValueError:
                    continue
                if common != self.project_root:
                    continue
                if os.path.isfile(abs_path):
                    explicit_files.append(abs_path)

        if include_globs:
            for pattern in include_globs:
                glob_pattern = os.path.join(self.project_root, pattern)
                for abs_path in sorted(glob.glob(glob_pattern, recursive=True)):
                    if os.path.isfile(abs_path):
                        explicit_files.append(os.path.abspath(abs_path))

        if not focus_files and not include_globs:
            git_files = self._get_git_files()
            if git_files:
                explicit_files = git_files

        if explicit_files:
            for abs_path in sorted(set(explicit_files)):
                if not os.path.isfile(abs_path):
                    continue
                fname = os.path.basename(abs_path)
                if not focus_files and not include_globs:
                    # Apply extension filtering to the default git/walk files
                    if not self._should_include_file(fname):
                        continue

                rel_path = os.path.relpath(abs_path, self.project_root)
                if self._in_excluded_dir(rel_path):
                    continue
                if self._should_exclude_file(rel_path, os.path.basename(abs_path)):
                    continue
                if self._is_denied(rel_path, deny_globs):
                    continue

                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                        content = handle.read()
                except OSError:
                    continue

                content = mask_secrets(content)

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
                self.stats.included_files.append(rel_path)

        else:
            for root, dirs, files in os.walk(self.project_root):
                # Prune excluded directories in-place for performance
                dirs[:] = [d for d in dirs if not self._should_exclude_dir(d)]

                for fname in sorted(files):
                    if not self._should_include_file(fname):
                        continue

                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, self.project_root)
                    if self._should_exclude_file(rel_path, fname):
                        continue
                    if self._is_denied(rel_path, deny_globs):
                        continue

                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                            content = handle.read()
                    except OSError:
                        continue

                    content = mask_secrets(content)

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
                    self.stats.included_files.append(rel_path)

                if total_chars >= max_chars:
                    break

        self.stats.chars_collected = total_chars
        return "".join(context_parts)

    def collect_project_files(self, max_chars: int = 250_000) -> str:
        """
        Backward-compatible alias for collect_project_context.
        """
        return self.collect_project_context(max_chars=max_chars)

    def generate_tree_view(self) -> str:
        """
        Returns a text representation of the folder structure, respecting .gitignore.
        """
        git_files = self._get_git_files()
        if git_files is None:
            # Fallback to os.walk if git is not available
            return self._generate_tree_walk(self.project_root)

        tree = {}
        for abs_path in git_files:
            rel_path = os.path.relpath(abs_path, self.project_root)
            if self._in_excluded_dir(rel_path):
                continue
            parts = rel_path.split(os.sep)
            curr = tree
            for part in parts:
                if part not in curr:
                    curr[part] = {}
                curr = curr[part]

        lines = ["."]
        def _render(node, indent=""):
            items = sorted(node.items())
            for idx, (name, children) in enumerate(items):
                is_last = (idx == len(items) - 1)
                connector = "└── " if is_last else "├── "
                lines.append(f"{indent}{connector}{name}")
                if children:
                    new_indent = indent + ("    " if is_last else "│   ")
                    _render(children, new_indent)

        _render(tree)
        return "\n".join(lines)

    def _generate_tree_walk(self, root_dir: str, indent: str = "") -> str:
        lines = []
        if indent == "":
            lines.append(".")

        try:
            entries = sorted(os.listdir(root_dir))
        except OSError:
            return ""

        entries = [e for e in entries if not self._should_exclude_dir(e)]

        for idx, entry in enumerate(entries):
            is_last = (idx == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            abs_path = os.path.join(root_dir, entry)
            lines.append(f"{indent}{connector}{entry}")

            if os.path.isdir(abs_path):
                new_indent = indent + ("    " if is_last else "│   ")
                subtree = self._generate_tree_walk(abs_path, new_indent)
                if subtree:
                    lines.append(subtree)

        return "\n".join(lines)

    def get_project_structure(self) -> str:
        """
        Returns a text tree of src/, config/, and tests/.
        """
        git_files = self._get_git_files()
        tree = {}
        target_dirs = {"src", "config", "tests"}

        files_to_process = []
        if git_files:
            for abs_path in git_files:
                rel_path = os.path.relpath(abs_path, self.project_root)
                parts = rel_path.split(os.sep)
                if parts[0] in target_dirs:
                    files_to_process.append(rel_path)
        else:
            # Fallback to manual walk if not in git
            for target in target_dirs:
                target_path = os.path.join(self.project_root, target)
                if os.path.isdir(target_path):
                    for root, _, files in os.walk(target_path):
                        for f in files:
                            abs_p = os.path.join(root, f)
                            rel_p = os.path.relpath(abs_p, self.project_root)
                            files_to_process.append(rel_p)

        for rel_path in files_to_process:
            if self._in_excluded_dir(rel_path):
                continue
            parts = rel_path.split(os.sep)
            curr = tree
            for part in parts:
                if part not in curr:
                    curr[part] = {}
                curr = curr[part]

        lines = ["."]
        def _render(node, indent=""):
            items = sorted(node.items())
            for idx, (name, children) in enumerate(items):
                is_last = (idx == len(items) - 1)
                connector = "└── " if is_last else "├── "
                lines.append(f"{indent}{connector}{name}")
                if children:
                    new_indent = indent + ("    " if is_last else "│   ")
                    _render(children, new_indent)

        _render(tree)
        return "\n".join(lines)

    def read_all_code(self) -> str:
        """
        Recursively reads .py and .yaml files (respecting .gitignore).
        """
        original_exts = self.include_exts
        self.include_exts = {".py", ".yaml", ".yml"}

        try:
            return self.collect_project_context(
                max_chars=1_000_000,
                include_globs=["src/**/*", "config/**/*", "tests/**/*"]
            )
        finally:
            self.include_exts = original_exts

    def read_all_source_files(self) -> str:
        """
        Gathers ALL .py files in src/ (respecting .gitignore and excluding secrets).
        """
        # Temporarily override include_exts to only include .py
        original_exts = self.include_exts
        self.include_exts = {".py"}

        try:
            # Context collection for architect mode usually needs more tokens
            # We use include_globs to target src/ while keeping project_root at the base
            # so that relative paths remain correct (e.g., src/quantum_edge_core/...)
            context = self.collect_project_context(
                max_chars=1_000_000,
                include_globs=["src/**/*.py"]
            )

            # If src/ was empty or missing, try root .py files as fallback
            if not context:
                context = self.collect_project_context(max_chars=1_000_000)

            return context
        finally:
            self.include_exts = original_exts


def collect_project_context(
    project_root: str,
    max_chars: int = 250_000,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
    include_globs: Optional[List[str]] = None,
    focus_files: Optional[List[str]] = None,
    deny_globs: Optional[List[str]] = None,
) -> str:
    """
    Functional wrapper to collect project context without instantiating the class directly.
    """
    scanner = ProjectScanner(project_root, include_exts=include_patterns, exclude_dirs=exclude_dirs)
    return scanner.collect_project_context(
        max_chars=max_chars,
        include_globs=include_globs,
        focus_files=focus_files,
        deny_globs=deny_globs,
    )
