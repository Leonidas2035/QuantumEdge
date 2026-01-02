import os
import runpy
import sys


def main() -> int:
    package_dir = os.path.abspath(os.path.dirname(__file__))
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    target = os.path.join(package_dir, "meta_agent.py")
    if not os.path.exists(target):
        print(f"[ERROR] meta_agent entrypoint not found: {target}")
        return 1
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
