import os
import json
import ast
import yaml


def get_python_summary(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content)
        docstring = ast.get_docstring(tree)
        classes = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]
        functions = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ]

        summary = ""
        if docstring:
            summary += docstring.split("\n")[0] + " "
        if classes:
            summary += f"Classes: {', '.join(classes)}. "
        if functions:
            summary += f"Functions: {', '.join(functions[:5])}" + (
                "..." if len(functions) > 5 else "."
            )
        return summary.strip() or "No summary available."
    except Exception as e:
        return f"Error parsing: {e}"


def get_yaml_summary(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            keys = list(data.keys())
            return f"YAML config with keys: {', '.join(keys[:10])}" + (
                "..." if len(keys) > 10 else "."
            )
        return "YAML file."
    except Exception as e:
        return f"Error parsing YAML: {e}"


directories = ["MarketDataHub", "SupervisorAgent", "ai_scalper_bot"]
cache = {}

for d in directories:
    if os.path.exists(d):
        for root, _, files in os.walk(d):
            for file in files:
                filepath = os.path.join(root, file)
                if file.endswith(".py"):
                    cache[filepath] = get_python_summary(filepath)
                elif file.endswith(".yaml") or file.endswith(".yml"):
                    cache[filepath] = get_yaml_summary(filepath)

with open(".jules_context_cache.json", "w", encoding="utf-8") as f:
    json.dump(cache, f, indent=2, ensure_ascii=False)

print(f"Scanned {len(cache)} files and saved to .jules_context_cache.json")
