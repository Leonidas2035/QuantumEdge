import os
import re

# We will apply conservative exact-line replacement for unused imports and variables based on flake8 output.

removals = {
    "src/quantum_edge_core/supervisor/supervisor.py": [
        ("from supervisor.llm.chat_client import ChatCompletionsClient", "")
    ],
    "src/quantum_edge_core/supervisor/supervisor/data_ingest.py": [
        ("current_ver = self.hub_version", "")
    ],
    "src/quantum_edge_core/supervisor/supervisor/heartbeat.py": [
        ("from dataclasses import dataclass, field", "from dataclasses import dataclass")
    ],
    "src/quantum_edge_core/supervisor/supervisor/risk_engine.py": [
        ("from typing import Any, Dict, List, Optional, Union", "from typing import Any, List, Optional"),
        ("import datetime", ""),
        ("import logging", ""),
        ("from supervisor.state import RiskStateSnapshot", "")
    ],
    "src/quantum_edge_infra/automation/meta_agent/__init__.py": [
        ("from .version import __version__", "")
    ],
    "src/quantum_edge_infra/automation/meta_agent/meta_agent.py": [
        ("from .projects_config import parse_projects_yaml, resolve_project_root", "from .projects_config import parse_projects_yaml")
    ],
    "src/quantum_edge_infra/automation/meta_agent/meta_gui.py": [
        ("from tkinter import messagebox, scrolledtext, ttk", "from tkinter import messagebox, scrolledtext"),
        ("project_choices = list(projects.keys())", "")
    ],
    "src/quantum_edge_infra/automation/meta_agent/offmarket_scheduler.py": [
        ("base_abs = resolve_project_root(\"quantum_edge_core\", proj_path)", "")
    ],
    "src/quantum_edge_infra/automation/meta_agent/supervisor_runner.py": [
        ("project_info = self.projects.get(\"supervisor\")", "")
    ]
}

# The remaining warnings are in scalper_v1, which the prompt didn't explicitly target, but we can clean them too.
removals["src/quantum_edge_core/strategies/scalper_v1/bot/core/config_loader.py"] = [
    ("from typing import Any, Dict, Optional", "from typing import Any, Dict")
]
removals["src/quantum_edge_core/strategies/scalper_v1/bot/integrations/prom_metrics.py"] = [
    ("import time", "")
]
removals["src/quantum_edge_core/strategies/scalper_v1/bot/trading/bingx_executor.py"] = [
    ("from bot.engine.decision_engine import DecisionDirection", "")
]

for file_path, patterns in removals.items():
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        continue
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for old, new in patterns:
        content = content.replace(old, new)
        
    # Clean up any blank lines created by removing imports
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Cleaned {file_path}")
