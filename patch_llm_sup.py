import re

with open("src/quantum_edge_core/supervisor/supervisor/llm_supervisor.py", "r") as f:
    data = f.read()

data = re.sub(r'class TradingMode\(str, Enum\):\n    SCALP = "scalp"\n    DCA = "dca"\n    PASS = "pass"\n    NEUTRAL = "neutral"', r'class TradingMode(str, Enum):\n    SCALP = "scalp"\n    DCA = "dca"\n    PASS = "pass"\n    NEUTRAL = "neutral"\n    SPOT_GRID = "spot_grid"', data)

with open("src/quantum_edge_core/supervisor/supervisor/llm_supervisor.py", "w") as f:
    f.write(data)
