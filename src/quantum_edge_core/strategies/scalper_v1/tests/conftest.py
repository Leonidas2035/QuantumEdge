import sys
from pathlib import Path

# Add the scalper_v1 directory to the python path to resolve "bot.*" imports
scalper_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(scalper_path))
