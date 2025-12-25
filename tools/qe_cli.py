import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "supervisor-foreground": ROOT / "SupervisorAgent" / "supervisor.py",
    "bot-run": ROOT / "ai_scalper_bot" / "run_bot.py",
    "meta-run": ROOT / "meta_agent" / "meta_agent.py",
}


def build_env() -> dict:
    env = os.environ.copy()
    paths = [
        str(ROOT / "ai_scalper_bot"),
        str(ROOT / "SupervisorAgent"),
        str(ROOT / "meta_agent"),
    ]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("QE_ROOT", str(ROOT))
    return env


def run_target(target: Path, extra_args: List[str]) -> int:
    if not target.exists():
        print(f"[qe_cli] Missing target: {target}", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(target)] + extra_args
    return subprocess.call(cmd, env=build_env(), cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantumEdge CLI wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in TARGETS:
        sub = subparsers.add_parser(name)
        sub.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    extra = args.args
    if extra and extra[0] == "--":
        extra = extra[1:]

    return run_target(TARGETS[args.command], extra)


if __name__ == "__main__":
    raise SystemExit(main())
