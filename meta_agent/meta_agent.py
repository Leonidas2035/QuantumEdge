import argparse
import json
import os
import shutil
import sys
from typing import Dict, Tuple, Optional

import yaml

from codex_client import CodexClient
from file_manager import FileManager
from meta_core import run_task
from paths import (
    BASE_DIR,
    OUTPUT_DIR,
    PROMPTS_ARCHIVE_DIR,
    PROMPTS_DIR,
    REPORTS_DIR,
    STAGES_PATH,
    TASKS_DIR,
)
from project_scanner import ProjectScanner
from projects_config import load_project_registry, resolve_project_root
from prompt_builder import PromptBuilder
from supervisor_runner import run_supervisor_cycle
from task_archiver import archive_task_file
from task_manager import list_tasks

FRONT_MATTER_DELIMITER = "---"
ALLOWED_MODES = {"readonly", "write_dev", "write_prod"}
DEFAULT_TASK_FILE = os.path.join(TASKS_DIR, "task_current.md")
MAX_CONTEXT_CHARS = 250_000

try:
    from tools.qe_config import get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_paths = None


def _resolve_base_dir() -> str:
    env_root = os.getenv("QE_ROOT")
    if env_root:
        return env_root
    if get_qe_paths:
        try:
            return str(get_qe_paths()["qe_root"])
        except Exception:
            pass
    parent = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
    if os.path.isdir(os.path.join(parent, "config")) and os.path.isdir(os.path.join(parent, "ai_scalper_bot")):
        return parent
    return BASE_DIR


def _resolve_meta_config_path(path: Optional[str]) -> str:
    base = _resolve_base_dir()
    env_override = os.getenv("META_AGENT_CONFIG")
    candidate = env_override or path or "config/meta_agent.yaml"
    if os.path.isabs(candidate):
        return candidate
    return os.path.abspath(os.path.join(base, candidate))


def load_task_from_file(path: str) -> Tuple[Dict, str]:
    """
    Returns (metadata, body_text) parsed from a task file with YAML front matter.
    If no front matter is present, metadata is an empty dict and body is the full file.
    """
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()

    lines = content.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        return {}, content

    end_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == FRONT_MATTER_DELIMITER:
            end_index = idx
            break

    if end_index is None:
        return {}, content

    metadata_text = "\n".join(lines[1:end_index])
    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        raise yaml.YAMLError("Front matter must be a mapping.")
    body = "\n".join(lines[end_index + 1:]).strip()
    return metadata, body


class MetaAgent:
    def __init__(self, config_path: str = "config.json"):
        resolved = _resolve_meta_config_path(config_path)
        self.config = self._load_config(resolved)
        self.builder = PromptBuilder()
        self.mode = self._resolve_mode()
        self.client = CodexClient(mode=self.mode)
        projects_path = (self.config or {}).get("projects_path")
        self.project_registry = load_project_registry(projects_path) if projects_path else load_project_registry()

    def _load_config(self, path: str) -> Dict:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if path.lower().endswith((".yaml", ".yml")):
                    return yaml.safe_load(handle) or {}
                return json.load(handle)
        except (json.JSONDecodeError, yaml.YAMLError, OSError):
            return {}

    def _resolve_mode(self) -> str:
        """
        Determines execution mode (dev/prod) based on env or config.
        """
        env_mode = os.getenv("META_AGENT_MODE")
        cfg_mode = (self.config or {}).get("mode")
        mode = (env_mode or cfg_mode or "dev").strip().lower()
        if mode not in {"dev", "prod"}:
            print(f"[WARN] Unsupported mode '{mode}', defaulting to dev.")
            mode = "dev"
        return mode

    def _resolve_output_path(self, output_path: str | None, task_id: str) -> str:
        if output_path:
            dest = os.path.abspath(output_path)
        else:
            dest = os.path.abspath(os.path.join("output", f"{task_id}_response.md"))

        # Keep outputs inside the Meta-Agent directory for predictability.
        meta_root = os.path.abspath(os.getcwd())
        try:
            common = os.path.commonpath([dest, meta_root])
        except ValueError:
            common = ""

        if common != meta_root:
            print("[WARN] output_path is outside the Meta-Agent directory; redirecting to output/.")
            dest = os.path.abspath(os.path.join("output", os.path.basename(dest)))

        return dest

    def _load_stages(self, path: str = STAGES_PATH):
        if not os.path.exists(path):
            print(f"[ERROR] stages file not found: {path}")
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or []
        except yaml.YAMLError as exc:
            print(f"[ERROR] Failed to parse stages file: {exc}")
            return []

    def run_stage_pipeline(self, override_project_id: Optional[str] = None) -> tuple[bool, list]:
        print("[INFO] Starting stage pipeline from stages.yaml...")
        stages = self._load_stages()
        if not stages:
            print("[WARN] No stages to run.")
            return False, []

        default_project_id = self.project_registry.default_project_id
        for stage in stages:
            name = stage.get("name", "unnamed_stage")
            prompt_file = stage.get("prompt")
            if not prompt_file:
                print(f"[ERROR] Stage {name} is missing a prompt path.")
                return False, stages

            project_id = override_project_id or stage.get("project") or default_project_id
            try:
                project_info = resolve_project_root(project_id, self.project_registry)
            except KeyError as exc:
                print(f"[ERROR] {exc}")
                return False, stages
            target_project = project_info.root_path
            if not os.path.isdir(target_project):
                print(f"[ERROR] Target project path does not exist for project_id={project_id}: {target_project}")
                return False, stages

            resolved_prompt = os.path.abspath(prompt_file)
            if not os.path.exists(resolved_prompt):
                alternative = os.path.abspath(os.path.join("prompts", prompt_file))
                if os.path.exists(alternative):
                    resolved_prompt = alternative
                else:
                    print(f"[ERROR] Prompt file not found for stage {name}: {prompt_file}")
                    return False, stages

            print(f"[INFO] Running stage: {name} using {prompt_file}")
            try:
                with open(resolved_prompt, "r", encoding="utf-8") as handle:
                    stage_instructions = handle.read()

                print(f"[INFO] Collecting project context from {target_project} for stage {name} (project_id={project_id})...")
                scanner = ProjectScanner(target_project)
                context = scanner.collect_project_context(max_chars=MAX_CONTEXT_CHARS)
                print(
                    f"[INFO] Collected context for stage {name}: "
                    f"{scanner.stats.files_included} files, {scanner.stats.chars_collected} chars."
                )

                full_prompt = self.builder.build_prompt(
                    stage_instructions,
                    context,
                    {
                        "stage": name,
                        "mode": "legacy",
                        "target_project": target_project,
                        "project_id": project_id,
                        "project_path": target_project,
                    },
                )

                print(f"[INFO] Sending prompt to Codex for stage {name}...")
                response = self.client.send(full_prompt)
                print(f"[INFO] Codex response received for stage {name}.")

                if isinstance(response, str) and response.lstrip().startswith("[ERROR]"):
                    print(f"[ERROR] Codex call failed for stage {name}: {response}")
                    return False, stages

                file_manager = FileManager(base_output_dir=OUTPUT_DIR, target_project=str(target_project), mode="write_dev")
                file_manager.process_output(response)
                print(f"[INFO] Stage {name} completed.")
            except Exception as exc:
                print(f"[ERROR] Stage {name} failed: {exc}")
                return False, stages

        return True, stages

    def run_task_file(self, task_path: str) -> bool:
        """
        Legacy wrapper that routes task execution through meta_core.run_task.
        """
        result = run_task(task_path)
        if result.get("status") != "ok":
            print(f"[ERROR] Task {result.get('task_id')} failed: {result.get('error_message')}")
            return False

        print(f"[INFO] Task {result.get('task_id')} completed.")
        if result.get("summary"):
            print(f"[INFO] Summary: {result['summary']}")
        if result.get("changed_files"):
            print(f"[INFO] Changed files ({len(result['changed_files'])}): {', '.join(result['changed_files'])}")
        if result.get("created_files"):
            print(f"[INFO] Created files ({len(result['created_files'])}): {', '.join(result['created_files'])}")
        if result.get("report_md_path"):
            print(f"[INFO] Markdown report: {result['report_md_path']}")
        if result.get("report_json_path"):
            print(f"[INFO] JSON report: {result['report_json_path']}")
        try:
            archive_task_file(os.path.abspath(task_path), target_project=result.get("meta", {}).get("target_project"))
            print(f"[INFO] Archived task file: {task_path}")
        except Exception as exc:
            print(f"[WARN] Failed to archive task file {task_path}: {exc}")
        return True


def parse_args():
    parser = argparse.ArgumentParser(description="Meta-Agent CLI")
    parser.add_argument("--config", dest="config_path", help="Path to meta-agent config (YAML/JSON).")
    parser.add_argument("--mode", default="auto", help="Execution mode (stages|task) or supervisor cadence (daily|weekly|adhoc|auto).")
    parser.add_argument("--task", dest="task_path", help="Path to a .md task file for task mode.")
    parser.add_argument("--task-id", dest="task_id", help="Task ID to resolve in tasks/<ID>.md for task mode.")
    parser.add_argument("--task-file", dest="task_file", help="Alias for --task (legacy).")
    parser.add_argument("--list-tasks", action="store_true", help="List available tasks from tasks/ directory.")
    parser.add_argument("--list-projects", action="store_true", help="List configured projects.")
    parser.add_argument("--project", dest="filter_project", help="Filter tasks by project when listing.")
    parser.add_argument("--task-type", dest="filter_task_type", help="Filter tasks by task type when listing.")
    parser.add_argument("--supervisor-goal", dest="supervisor_goal", help="Run a supervisor goal (high-level string).")
    parser.add_argument("--supervisor-project", dest="supervisor_project", help="Project root for supervisor goal runs.", default="ai_scalper_bot")
    parser.add_argument("--project-id", dest="stage_project_id", help="Override project id for stage pipeline.")
    parser.add_argument("--once", action="store_true", help="Run once and exit (default behavior).")
    return parser.parse_args()


def cleanup_after_successful_run(stages: list) -> None:
    """
    Archives prompt files, clears stages.yaml, and moves reports into output/.
    """
    os.makedirs(PROMPTS_ARCHIVE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Archive prompt files used in this run
    for stage in stages:
        prompt_path = stage.get("prompt")
        if not prompt_path:
            continue
        if not os.path.isabs(prompt_path):
            prompt_path = os.path.join(BASE_DIR, prompt_path)
        if not os.path.exists(prompt_path):
            continue
        dest = os.path.join(PROMPTS_ARCHIVE_DIR, os.path.basename(prompt_path))
        try:
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(prompt_path, dest)
            print(f"[INFO] Archived prompt: {dest}")
        except Exception as exc:
            print(f"[WARN] Failed to archive prompt {prompt_path}: {exc}")

    # Clear stages.yaml
    try:
        with open(STAGES_PATH, "w", encoding="utf-8") as handle:
            handle.write("[]\n")
        print("[INFO] Cleared stages.yaml")
    except Exception as exc:
        print(f"[WARN] Failed to clear stages.yaml: {exc}")

    # Move reports into output/
    if os.path.isdir(REPORTS_DIR):
        for root, _, files in os.walk(REPORTS_DIR):
            rel_root = os.path.relpath(root, REPORTS_DIR)
            for filename in files:
                src = os.path.join(root, filename)
                dest_dir = os.path.join(OUTPUT_DIR, rel_root) if rel_root != "." else OUTPUT_DIR
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, filename)
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(src, dest)
                    print(f"[INFO] Moved report: {dest}")
                except Exception as exc:
                    print(f"[WARN] Failed to move report {src}: {exc}")


def main() -> int:
    args = parse_args()
    if args.config_path:
        os.environ["META_AGENT_CONFIG"] = args.config_path

    if args.once:
        print("[INFO] --once specified; running a single pass.")

    if args.list_projects:
        registry = load_project_registry()
        print("Configured projects:")
        for pid, info in registry.projects.items():
            prefix = "(default) " if pid == registry.default_project_id else ""
            print(f"- {prefix}{pid}: {info.root_path} - {info.description}")
        return 0

    if args.list_tasks:
        try:
            tasks = list_tasks(project=args.filter_project, task_type=args.filter_task_type)
        except Exception as exc:
            print(f"[ERROR] Failed to list tasks: {exc}")
            return 1
        if not tasks:
            print("No tasks found.")
            return 0
        header = f"{'TASK_ID':30} {'PROJECT':18} {'TYPE':14} TITLE"
        print(header)
        print("-" * len(header))
        for task in tasks:
            print(f"{task.task_id:30} {task.project:18} {task.task_type:14} {task.title}")
        return 0

    if args.supervisor_goal:
        try:
            sup_result = run_supervisor_cycle(
                goal=args.supervisor_goal,
                mode=args.mode or "daily",
                project=getattr(args, "supervisor_project", None) or "ai_scalper_bot",
            )
        except Exception as exc:
            print(f"[ERROR] Supervisor run failed: {exc}")
            return 1

        ok_count = sum(1 for r in sup_result.get("tasks", []) if r.get("status") == "ok")
        err_count = sum(1 for r in sup_result.get("tasks", []) if r.get("status") == "error")
        partial_count = sum(1 for r in sup_result.get("tasks", []) if r.get("status") == "partial")

        print("[INFO] Supervisor run completed.")
        print(f"  Goal: {sup_result.get('goal')}")
        print(f"  Mode: {sup_result.get('mode')}")
        print(f"  Project: {sup_result.get('project')}")
        print(f"  Status: {sup_result.get('status')}")
        print(f"  Tasks total: {len(sup_result.get('tasks', []))} (ok/partial/error: {ok_count}/{partial_count}/{err_count})")
        if sup_result.get("supervisor_md_path"):
            print(f"  Summary (MD): {sup_result.get('supervisor_md_path')}")
        if sup_result.get("supervisor_json_path"):
            print(f"  Summary (JSON): {sup_result.get('supervisor_json_path')}")
        if sup_result.get("overall_summary"):
            print(f"  Overall summary: {sup_result.get('overall_summary')}")
        return 0 if sup_result.get("status") == "ok" else 1

    # Resolve task identifier/path if provided
    task_identifier = args.task_path or args.task_file or args.task_id

    allowed_modes = {"auto", "stages", "task"}
    run_mode = args.mode if args.mode in allowed_modes else "auto"
    task_mode = run_mode == "task" or bool(task_identifier)

    try:
        if task_mode:
            if not task_identifier:
                print("[ERROR] Task mode requested but no --task/--task-id provided.")
                return 1
            result = run_task(task_identifier)
            print(f"[INFO] Task {result.get('task_id')} status: {result.get('status')}")
            if result.get("summary"):
                print(f"[INFO] Summary: {result['summary']}")
            if result.get("error_message"):
                print(f"[ERROR] {result['error_message']}")
            if result.get("changed_files"):
                print(f"[INFO] Changed files ({len(result['changed_files'])}): {', '.join(result['changed_files'])}")
            if result.get("created_files"):
                print(f"[INFO] Created files ({len(result['created_files'])}): {', '.join(result['created_files'])}")
            if result.get("report_md_path"):
                print(f"[INFO] Markdown report: {result['report_md_path']}")
            if result.get("report_json_path"):
                print(f"[INFO] JSON report: {result['report_json_path']}")
            return 0 if result.get("status") == "ok" else 1

        agent = MetaAgent()
        success, stages = agent.run_stage_pipeline(override_project_id=args.stage_project_id)
    except Exception as exc:
        print(f"[ERROR] Meta-Agent failed: {exc}")
        return 1

    if success:
        cleanup_after_successful_run(stages)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
