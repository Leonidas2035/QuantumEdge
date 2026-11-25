import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(__file__).resolve().parents[3]
for p in (SRC_ROOT, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
