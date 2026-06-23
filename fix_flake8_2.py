import os
import re

removals = {
    "src/quantum_edge_core/strategies/scalper_v1/bot/ml/runtime/model_manager.py": [
        (
            "from typing import Any, Dict, List, Optional, Tuple",
            "from typing import Any, Dict, List, Optional",
        )
    ],
    "src/quantum_edge_core/strategies/scalper_v1/bot/policy/policy_contract.py": [
        ("from typing import Dict, Any, Optional", "from typing import Dict, Any")
    ],
    "src/quantum_edge_core/strategies/scalper_v1/bot/run_bot.py": [
        ("ml_compat_strict = ", "# ml_compat_strict = "),
        ("min_edge = ", "# min_edge = "),
    ],
    "src/quantum_edge_core/strategies/scalper_v1/bot/trading/bingx_executor.py": [
        ("side = ", "# side = ")
    ],
    "src/quantum_edge_core/strategies/scalper_v1/bot/trading/executor.py": [
        ("side = ", "# side = ")
    ],
    "src/quantum_edge_core/strategies/scalper_v1/tests/test_ml_stage3_eval_outputs.py": [
        ("model = ", "# model = ")
    ],
    "/home/korben/.hermes/hermes/research/offline/scalper_bot/scenarios/validate.py": [
        ("episodes_dir = ", "# episodes_dir = ")
    ],
    "/home/korben/.hermes/hermes/research/sandbox/offline_loop.py": [
        ("pseudo_signal = ", "# pseudo_signal = ")
    ],
    "/home/korben/.hermes/hermes/service.py": [("risk_info = ", "# risk_info = ")],
    "/home/korben/.hermes/hermes/supervisor/data_ingest.py": [
        ("current_ver = ", "# current_ver = ")
    ],
    "src/quantum_edge_infra/automation/meta_agent/meta_agent.py": [
        (
            "from .projects_config import parse_projects_yaml, resolve_project_root",
            "from .projects_config import parse_projects_yaml",
        )
    ],
    "src/quantum_edge_infra/automation/meta_agent/meta_gui.py": [
        ("project_choices = ", "# project_choices = ")
    ],
    "src/quantum_edge_infra/automation/meta_agent/offmarket_scheduler.py": [
        ("base_abs = ", "# base_abs = ")
    ],
    "src/quantum_edge_infra/automation/meta_agent/supervisor_runner.py": [
        ("project_info = ", "# project_info = ")
    ],
}

for file_path, patterns in removals.items():
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        continue
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    for old, new in patterns:
        content = content.replace(old, new)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Cleaned {file_path}")
