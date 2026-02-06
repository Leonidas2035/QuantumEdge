import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FileChange:
    path: str               # relative to project root
    old_content: str
    new_content: str


@dataclass
class ChangeSet:
    project_root: str
    changes: Dict[str, FileChange] = field(default_factory=dict)


class FileManager:
    FILE_PATTERN = r"===FILE:\s*(.*?)===\n(.*?)(?=\n===FILE:|$)"

    def __init__(self, base_output_dir: str = "output", target_project: str | None = None, mode: str = "write_dev"):
        self.base_output_dir = os.path.abspath(base_output_dir)
        self.target_project = os.path.abspath(target_project) if target_project else None
        self.mode = mode

    def _ensure_dir(self, path: str) -> None:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)

    @staticmethod
    def normalize_output_path(relative_path: str) -> str:
        """
        Normalizes an output path to avoid nesting under output/output or reports/.

        Example:
            reports/foo.md -> foo.md
            output/bar.txt -> bar.txt
        """
        cleaned = relative_path.strip().lstrip("/\\")
        for prefix in ("reports", "output"):
            if cleaned == prefix:
                cleaned = ""
                break
            prefix_with_sep = prefix + os.sep
            if cleaned.startswith(prefix_with_sep):
                cleaned = cleaned[len(prefix_with_sep) :]
                break
        return cleaned

    def _resolve_destination(self, declared_path: str) -> str:
        normalized = os.path.normpath(declared_path.strip())

        # Redirect everything to output when in readonly mode.
        if self.mode == "readonly" or not self.target_project:
            cleaned = self.normalize_output_path(normalized)
            cleaned = cleaned.lstrip("/\\")
            return os.path.join(self.base_output_dir, cleaned)

        if os.path.isabs(normalized):
            abs_path = os.path.abspath(normalized)
            try:
                common = os.path.commonpath([abs_path, self.target_project])
            except ValueError:
                common = ""
            if common == self.target_project:
                return abs_path
            # Outside the target project, so redirect to output for safety.
            safe_name = normalized.lstrip("\\/").replace(":", "")
            return os.path.join(self.base_output_dir, safe_name)

        return os.path.join(self.target_project, normalized)

    def _display_path(self, destination: str) -> str:
        """
        Returns a readable path, preferring project-relative paths when possible.
        """
        abs_dest = os.path.abspath(destination)

        if self.target_project:
            target_root = os.path.abspath(self.target_project)
            try:
                common = os.path.commonpath([abs_dest, target_root])
            except ValueError:
                common = ""
            if common == target_root:
                return os.path.relpath(abs_dest, target_root)

        try:
            common_output = os.path.commonpath([abs_dest, self.base_output_dir])
        except ValueError:
            common_output = ""
        if common_output == self.base_output_dir:
            return os.path.relpath(abs_dest, self.base_output_dir)

        return abs_dest

    def process_output(self, response: str) -> dict:
        """
        Deprecated: legacy direct write path. Prefer ChangeSet-based flow.
        """
        from warnings import warn

        warn("process_output is deprecated; use ChangeSet helpers instead.", DeprecationWarning)
        matches = re.findall(self.FILE_PATTERN, response, flags=re.S | re.M)
        written_files: list[str] = []
        created_files: list[str] = []
        changed_files: list[str] = []
        for path, code in matches:
            dest = self._resolve_destination(path)
            existed_before = os.path.exists(dest)
            self._ensure_dir(dest)
            with open(dest, "w", encoding="utf-8") as handle:
                handle.write(code)

            display_path = self._display_path(dest)
            if display_path not in written_files:
                written_files.append(display_path)
            if existed_before:
                if display_path not in changed_files:
                    changed_files.append(display_path)
            else:
                if display_path not in created_files:
                    created_files.append(display_path)

            print(f"[WRITE] Created/updated: {dest}")

        return {
            "written_files": written_files,
            "created_files": created_files,
            "changed_files": changed_files,
        }


def build_change_set_from_response(project_root: str, model_output: str) -> ChangeSet:
    """
    Parses model output and builds a ChangeSet with old/new content.
    """
    project_root_abs = os.path.abspath(project_root)
    file_pattern = r"===FILE:\s*(.*?)===\n(.*?)(?=\n===FILE:|$)"
    matches = re.findall(file_pattern, model_output, flags=re.S | re.M)
    change_set = ChangeSet(project_root=project_root_abs, changes={})
    for path, code in matches:
        rel_path = os.path.normpath(path.strip())
        abs_path = os.path.join(project_root_abs, rel_path) if not os.path.isabs(rel_path) else os.path.abspath(rel_path)
        if os.path.commonpath([abs_path, project_root_abs]) != project_root_abs:
            # Skip files outside project root for safety
            continue
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                old_content = handle.read()
        except OSError:
            old_content = ""
        change_set.changes[rel_path] = FileChange(path=rel_path, old_content=old_content, new_content=code)
    return change_set


def apply_change_set_direct(change_set: ChangeSet) -> Dict[str, List[str]]:
    """
    Applies changes directly to disk (legacy mode).
    """
    changed_files: List[str] = []
    created_files: List[str] = []
    deleted_files: List[str] = []

    for rel_path, change in change_set.changes.items():
        abs_path = os.path.join(change_set.project_root, rel_path)
        existed = os.path.exists(abs_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(change.new_content)
        if existed:
            changed_files.append(rel_path)
        else:
            created_files.append(rel_path)

    return {
        "changed_files": changed_files,
        "created_files": created_files,
        "deleted_files": deleted_files,
        "patch_files": [],
    }


def _write_patch_file(base_dir: str, rel_path: str, old: str, new: str) -> str:
    """
    Writes a unified diff patch file and returns its path.
    """
    abs_patch_path = os.path.join(base_dir, f"{rel_path}.patch")
    os.makedirs(os.path.dirname(abs_patch_path), exist_ok=True)
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    )
    with open(abs_patch_path, "w", encoding="utf-8") as handle:
        handle.writelines(diff)
    return abs_patch_path


def write_change_set_as_patches(change_set: ChangeSet, patches_dir: str) -> Dict[str, List[str]]:
    """
    Writes change set as patch files (patch-only mode).
    """
    patches_dir_abs = os.path.abspath(patches_dir)
    os.makedirs(patches_dir_abs, exist_ok=True)

    patch_files: List[str] = []
    changed_files: List[str] = []
    created_files: List[str] = []
    deleted_files: List[str] = []

    for rel_path, change in change_set.changes.items():
        patch_path = _write_patch_file(patches_dir_abs, rel_path, change.old_content, change.new_content)
        patch_files.append(patch_path)
        if change.old_content == "":
            created_files.append(rel_path)
        else:
            changed_files.append(rel_path)

    return {
        "patch_files": patch_files,
        "changed_files": changed_files,
        "created_files": created_files,
        "deleted_files": deleted_files,
    }
