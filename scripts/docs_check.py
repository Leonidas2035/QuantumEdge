import os
import subprocess
import sys

DOC_PATHS = [
    "README.md",
    "docs/architecture.md",
    "docs/operations.md",
    "docs/tasks_contract.md",
    "docs/scheduler.md",
    "docs/control_center.md",
    "docs/security.md",
    "docs/CHANGELOG.md",
    "docs/upgrade.md",
]

SUBCOMMANDS = [
    ["diag"],
    ["health"],
    ["status", "--help"],
    ["run-task", "--help"],
    ["watch", "--help"],
    ["run-scheduler", "--help"],
    ["scheduler-status", "--help"],
    ["ui", "--help"],
    ["approve-apply", "--help"],
    ["dump-run", "--help"],
    ["version"],
]


def check_docs_exist() -> list[str]:
    return [path for path in DOC_PATHS if not os.path.exists(path)]


def check_subcommands() -> list[str]:
    failures = []
    for cmd in SUBCOMMANDS:
        proc = subprocess.run(
            [sys.executable, "meta_agent.py", *cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode not in (0, 2):
            failures.append(f"{' '.join(cmd)} (code={proc.returncode})")
    return failures


def main() -> int:
    missing = check_docs_exist()
    if missing:
        print("Missing docs:")
        for path in missing:
            print(f"- {path}")
        return 1

    failures = check_subcommands()
    if failures:
        print("Subcommand checks failed:")
        for entry in failures:
            print(f"- {entry}")
        return 1

    print("Docs check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
