import os
import runpy
import sys


def main() -> int:
    base = os.path.abspath(os.path.dirname(__file__))
    target = os.path.join(base, "meta_agent", "meta_agent.py")
    if not os.path.exists(target):
        print(f"[ERROR] meta_agent entrypoint not found: {target}")
        return 1
    sys.path.insert(0, os.path.join(base, "meta_agent"))
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
