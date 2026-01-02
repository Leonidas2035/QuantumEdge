from dataclasses import dataclass
from typing import List, Optional

from file_manager import (
    ChangeSet,
    apply_change_set_direct,
    write_change_set_as_patches,
)
from safety_policy import SafetyEvaluation, SafetyPolicy, evaluate_change_set, load_safety_policy


@dataclass
class WriteOutcome:
    status: str
    error_message: Optional[str]
    applied: bool
    write_mode_used: str
    changed_files: List[str]
    created_files: List[str]
    deleted_files: List[str]
    patch_files: List[str]
    safety_eval: SafetyEvaluation


def apply_change_set_with_policy(
    change_set: ChangeSet,
    patches_dir: str,
    policy: SafetyPolicy | None = None,
) -> WriteOutcome:
    safety_policy = policy or load_safety_policy()
    safety_eval = evaluate_change_set(safety_policy, change_set)

    should_patch = safety_eval.write_mode == "patch_only" or safety_eval.overall_verdict in {"warn", "block"}
    apply_result = {
        "changed_files": [],
        "created_files": [],
        "deleted_files": [],
        "patch_files": [],
    }

    applied = False
    write_mode_used = "patch_only" if should_patch else "direct"
    status = "ok"
    error_message = None

    if safety_eval.overall_verdict == "block":
        status = "blocked"
        error_message = "Changes blocked by safety policy."
    elif safety_eval.overall_verdict == "warn":
        status = "partial"
        error_message = "Changes require review due to safety warnings."

    if should_patch:
        apply_result = write_change_set_as_patches(change_set, patches_dir)
    elif safety_eval.overall_verdict == "allow":
        applied = True
        apply_result = apply_change_set_direct(change_set)

    return WriteOutcome(
        status=status,
        error_message=error_message,
        applied=applied,
        write_mode_used=write_mode_used,
        changed_files=apply_result.get("changed_files", []),
        created_files=apply_result.get("created_files", []),
        deleted_files=apply_result.get("deleted_files", []),
        patch_files=apply_result.get("patch_files", []),
        safety_eval=safety_eval,
    )
