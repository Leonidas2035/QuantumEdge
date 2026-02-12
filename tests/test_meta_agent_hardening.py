import sys
from pathlib import Path
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
META_AGENT_DIR = ROOT_DIR / "src" / "quantum_edge_infra" / "automation" / "meta_agent"
if str(META_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(META_AGENT_DIR))

from file_manager import ChangeSet, FileChange, build_change_set_from_response
from safety_policy import FileSafetyStatus, SafetyEvaluation, SafetyPolicy
from write_engine import WriteOutcome, apply_change_set_with_policy


def test_change_set_blocks_outside_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    response = (
        "===FILE: ../outside.txt===\nnope\n"
        "===FILE: inside.txt===\nok\n"
    )
    change_set = build_change_set_from_response(str(project_root), response)
    assert "inside.txt" in change_set.changes
    assert "outside.txt" not in change_set.changes


def test_warn_verdict_writes_patches_only(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    target_file = config_dir / "settings.yaml"
    target_file.write_text("old", encoding="utf-8")

    change_set = ChangeSet(
        project_root=str(project_root),
        changes={
            "config/settings.yaml": FileChange(
                path="config/settings.yaml",
                old_content="old",
                new_content="new",
            )
        },
    )
    policy = SafetyPolicy(
        project="test",
        default_write_mode="direct",
        max_files_changed=10,
        max_file_size_kb=64,
        protected_paths=[],
        warning_paths=["config/**"],
        allowed_paths=[],
    )

    patches_dir = tmp_path / "patches"
    outcome = apply_change_set_with_policy(change_set, str(patches_dir), policy)
    assert outcome.applied is False
    assert outcome.safety_eval.overall_verdict == "warn"
    assert target_file.read_text(encoding="utf-8") == "old"
    assert outcome.patch_files


def test_stage_pipeline_routes_through_safety_policy(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    project_root = base_dir / "project"
    project_root.mkdir()
    prompt_dir = base_dir / "prompts"
    prompt_dir.mkdir()
    prompt_file = prompt_dir / "stage_prompt.md"
    prompt_file.write_text("do something", encoding="utf-8")

    config_dir = base_dir / "config"
    config_dir.mkdir()
    projects_yaml = config_dir / "projects.yaml"
    projects_yaml.write_text(
        "default: test_project\n"
        "projects:\n"
        "  test_project:\n"
        "    path: project\n"
        "    description: test\n",
        encoding="utf-8",
    )

    stages_path = base_dir / "stages.yaml"
    stages_path.write_text(
        f"- name: stage_one\n  prompt: {prompt_file}\n  project: test_project\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("QE_ROOT", str(base_dir))
    monkeypatch.setenv("META_AGENT_PROJECTS_PATH", str(projects_yaml))

    # Ensure we import the correct meta_agent module from src/
    import importlib.util
    spec = importlib.util.spec_from_file_location("meta_agent_real", str(META_AGENT_DIR / "meta_agent.py"))
    meta_agent_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta_agent_mod)

    monkeypatch.setattr(meta_agent_mod, "PATCHES_DIR", str(tmp_path / "patches"))
    monkeypatch.setattr(meta_agent_mod, "write_json_report", lambda report: str(tmp_path / "report.json"))
    monkeypatch.setattr(meta_agent_mod, "write_md_report", lambda report: str(tmp_path / "report.md"))
    monkeypatch.setattr(meta_agent_mod.ProjectScanner, "collect_project_context", lambda self, max_chars=250000: "")
    monkeypatch.setattr(
        meta_agent_mod.MetaAgent,
        "_load_stages",
        lambda self, path=str(stages_path): yaml.safe_load(stages_path.read_text(encoding="utf-8")) or [],
    )

    class DummyClient:
        def __init__(self, provider=None, mode=None, model=None):
            self.model = model or "dummy"
            self.provider = provider or "dummy"

        def send(self, prompt: str) -> str:
            return "===FILE: test.txt===\ncontent\n"

    monkeypatch.setattr(meta_agent_mod, "LLMClient", DummyClient)

    called = {"value": False}

    def fake_apply(change_set, patches_dir):
        called["value"] = True
        safety_eval = SafetyEvaluation(
            write_mode="patch_only",
            overall_verdict="allow",
            files=[FileSafetyStatus(path="test.txt", verdict="allow", reasons=[])],
            reasons=[],
        )
        return WriteOutcome(
            status="ok",
            error_message=None,
            applied=False,
            write_mode_used="patch_only",
            changed_files=["test.txt"],
            created_files=[],
            deleted_files=[],
            patch_files=[],
            safety_eval=safety_eval,
        )

    monkeypatch.setattr(meta_agent_mod, "apply_change_set_with_policy", fake_apply)

    agent = meta_agent_mod.MetaAgent()
    success, _ = agent.run_stage_pipeline()
    assert success is True
    assert called["value"] is True
