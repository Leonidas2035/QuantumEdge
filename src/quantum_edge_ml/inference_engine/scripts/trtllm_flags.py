from __future__ import annotations

import argparse
import sys
from typing import List


def detect_kv_cache_flags(help_text: str) -> List[str]:
    """Return the kv cache flags based on trtllm-build --help output."""
    if "--kv_cache_type" in help_text:
        return ["--kv_cache_type", "paged"]
    if "--paged_kv_cache" in help_text:
        return ["--paged_kv_cache", "enable"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kv-cache-flags", action="store_true", help="Output kv cache flags"
    )
    args = parser.parse_args()

    help_text = sys.stdin.read()
    if args.kv_cache_flags:
        flags = detect_kv_cache_flags(help_text)
        sys.stdout.write(" ".join(flags))
        return 0

    parser.error("No action specified")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
