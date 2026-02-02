import os

class ProjectScanner:
    def __init__(self, target_dir="../ai_scalper_bot"):
        self.target = target_dir

    def collect_project_files(self):
        result = ""
        for root, _, files in os.walk(self.target):
            for f in files:
                if f.endswith((".py", ".yaml", ".json")):
                    path = os.path.join(root, f)
                    with open(path, "r", encoding="utf-8", errors="ignore") as fd:
                        code = fd.read()
                    rel = os.path.relpath(path, self.target)
                    result += f"\n===FILE: {rel}===\n{code}\n"
        return result
