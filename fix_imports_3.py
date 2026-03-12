import os
import re

directory = "src/quantum_edge_core/supervisor"

inner_modules = [
    "action_ledger",
    "ai_bridge",
    "alerts",
    "api",
    "api_server",
    "audit_report",
    "autopilot",
    "config",
    "config_loader",
    "context_builder",
    "contracts",
    "dashboard",
    "data_ingest",
    "episodes",
    "events",
    "gemini_client",
    "guards",
    "heartbeat",
    "ingest",
    "ipc",
    "llm",
    "llm_supervisor",
    "lockbot",
    "logging_setup",
    "meta_supervisor",
    "ops",
    "policy_store",
    "process_manager",
    "process_spec",
    "prompts",
    "regime_sm",
    "risk_engine",
    "run_context",
    "security",
    "snapshot_models",
    "state",
    "stats",
    "tasks",
    "tsdb",
    "utils",
]

# Create a regex to match quantum_edge_core.supervisor.(module)
mods_pattern = "|".join(inner_modules)
regex_from = re.compile(
    rf"\bfrom\s+quantum_edge_core\.supervisor\.(?P<mod>{mods_pattern})\b"
)
regex_import = re.compile(
    rf"\bimport\s+quantum_edge_core\.supervisor\.(?P<mod>{mods_pattern})\b"
)

changed_files = []

for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith(".py"):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r") as f:
                    content = f.read()

                new_content = regex_from.sub(
                    r"from quantum_edge_core.supervisor.supervisor.\g<mod>", content
                )
                new_content = regex_import.sub(
                    r"import quantum_edge_core.supervisor.supervisor.\g<mod>",
                    new_content,
                )

                if new_content != content:
                    with open(filepath, "w") as f:
                        f.write(new_content)
                    changed_files.append(filepath)
            except Exception as e:
                pass

print(f"Changed {len(changed_files)} files.")
for cf in changed_files:
    print(cf)
