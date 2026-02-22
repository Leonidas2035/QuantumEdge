import os
import subprocess
import datetime

class JulesLocalTools:
    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.audit_log = os.path.join(repo_path, "jules_audit.log")

    def log_summary(self, task, action_taken, status="SUCCESS"):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] TASK: {task}\n[{timestamp}] ACTION: {action_taken}\n[{timestamp}] STATUS: {status}\n{'-'*40}\n"
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(entry)

    def update_project_map(self):
        """Створює закешовану мапу проекту для економії токенів."""
        structure = []
        ignore = {'.git', 'venv', '__pycache__', 'dist', 'build', '.idea', '.vscode', '.jules_map.txt'}
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in ignore]
            level = root.replace(self.repo_path, '').count(os.sep)
            indent = ' ' * 4 * level
            structure.append(f"{indent}{os.path.basename(root)}/")
            for f in files:
                if not f.endswith(('.pyc', '.pyo', '.log')):
                    structure.append(f"{' ' * 4 * (level + 1)}{f}")
        
        # Зберігаємо мапу в корінь проекту
        map_path = os.path.join(self.repo_path, ".jules_map.txt")
        with open(map_path, "w", encoding="utf-8") as f:
            f.write("\n".join(structure))
        return ".jules_map.txt"

    def search_code(self, query):
        try:
            result = subprocess.run(
                ['grep', '-rn', '--exclude-dir=venv', query, self.repo_path],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout[:2000] if result.stdout else "Nothing found."
        except Exception as e:
            return f"Search error: {e}"

    def read_file(self, relative_path):
        full_path = os.path.join(self.repo_path, relative_path)
        if not os.path.exists(full_path):
            return "Error: File not found."
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    def write_file(self, relative_path, content):
        full_path = os.path.join(self.repo_path, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Updated {relative_path}"

    def check_syntax(self, relative_path):
        full_path = os.path.join(self.repo_path, relative_path)
        result = subprocess.run(['python3', '-m', 'py_compile', full_path], capture_output=True)
        if result.returncode != 0:
            return f"Syntax Error: {result.stderr.decode()}"
        return "Syntax OK"
