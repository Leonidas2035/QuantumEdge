import fnmatch
import os
from dataclasses import dataclass, field
from typing import List, Literal, Optional

import yaml

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SAFETY_POLICY_PATH = os.path.join(BASE_DIR, "config", "safety_policy.yaml")
WriteMode = Literal["patch_only", "direct"]


@dataclass
class SafetyPolicy:
    project: str
    default_write_mode: WriteMode
    max_files_changed: int
    max_file_size_kb: int
    protected_paths: List[str] = field(default_factory=list)
    warning_paths: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=list)


@dataclass
class FileSafetyStatus:
    path: str
    verdict: Literal["allow", "warn", "block"]
    reasons: List[str] = field(default_factory=list)


@dataclass
class SafetyEvaluation:
    write_mode: WriteMode
    overall_verdict: Literal["allow", "warn", "block"]
    files: List[FileSafetyStatus]
    reasons: List[str] = field(default_factory=list)


def _default_policy() -> SafetyPolicy:
    return SafetyPolicy(
        project="ai_scalper_bot",
        default_write_mode="patch_only",
        max_files_changed=20,
        max_file_size_kb=256,
        protected_paths=[
            ".env",
            "**/.env",
            "backup_secrets/**",
            "config/**secret**",
            "config/**key**",
            "**/secrets.env",
        ],
        warning_paths=[
            "config/**",
            "scripts/deploy/**",
        ],
        allowed_paths=[
            "bot/**",
            "ml/**",
            "tests/**",
            "strategies/**",
        ],
    )


def load_safety_policy(path: str = SAFETY_POLICY_PATH) -> SafetyPolicy:
    """
    Loads safety policy from YAML or returns defaults if missing/invalid.
    """
    if not os.path.exists(path):
        return _default_policy()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except (yaml.YAMLError, OSError):
        return _default_policy()

    policy = _default_policy()
    policy.project = raw.get("project", policy.project)
    policy.default_write_mode = raw.get("default_write_mode", policy.default_write_mode)
    if policy.default_write_mode not in ("patch_only", "direct"):
        policy.default_write_mode = "patch_only"
    policy.max_files_changed = int(raw.get("max_files_changed", policy.max_files_changed))
    policy.max_file_size_kb = int(raw.get("max_file_size_kb", policy.max_file_size_kb))
    policy.protected_paths = raw.get("protected_paths", policy.protected_paths)
    policy.warning_paths = raw.get("warning_paths", policy.warning_paths)
    policy.allowed_paths = raw.get("allowed_paths", policy.allowed_paths)
    return policy


def _match_any(path: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def evaluate_change_set(policy: SafetyPolicy, change_set) -> SafetyEvaluation:
    """
    Evaluates a ChangeSet against safety policy rules.
    """
    files_status: List[FileSafetyStatus] = []
    reasons: List[str] = []

    # File count limit
    if len(change_set.changes) > policy.max_files_changed:
        reasons.append(f"Changed files exceed max_files_changed={policy.max_files_changed}")

    for rel_path, change in change_set.changes.items():
        verdict = "allow"
        file_reasons: List[str] = []
        norm_path = rel_path.replace("\\", "/")

        if _match_any(norm_path, policy.protected_paths):
            verdict = "block"
            file_reasons.append("Matches protected_paths")

        if verdict != "block" and _match_any(norm_path, policy.warning_paths):
            verdict = "warn"
            file_reasons.append("Matches warning_paths")

        if verdict != "block" and policy.allowed_paths:
            if not _match_any(norm_path, policy.allowed_paths):
                verdict = "warn"
                file_reasons.append("Outside allowed_paths whitelist")

        # size check
        new_size_kb = len(change.new_content.encode("utf-8")) / 1024
        if new_size_kb > policy.max_file_size_kb:
            verdict = "warn" if verdict == "allow" else verdict
            file_reasons.append(f"New content exceeds {policy.max_file_size_kb} KB")

        files_status.append(FileSafetyStatus(path=rel_path, verdict=verdict, reasons=file_reasons))

    overall = "allow"
    if reasons:
        overall = "block"
    else:
        if any(f.verdict == "block" for f in files_status):
            overall = "block"
        elif any(f.verdict == "warn" for f in files_status):
            overall = "warn"

    return SafetyEvaluation(
        write_mode=policy.default_write_mode,
        overall_verdict=overall,
        files=files_status,
        reasons=reasons,
    )
