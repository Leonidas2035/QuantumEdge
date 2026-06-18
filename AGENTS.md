# QuantumEdge Agent Rules

- All Python modules must be launched from the project root via `python -m module_name` or absolute paths from `QuantumEdge.py` to avoid sys.path issues.

## TASK DELEGATION TO ANTIGRAVITY (agy)
You are running as a 120B model on OpenRouter and have strict token/performance limits.
If a task requires:
1. Writing or modifying python/yaml code files.
2. Debugging complex runtime crashes or traceback errors from logs.
3. Writing or running pytest test suites.

DO NOT attempt to write code or debug complex issues yourself.
Instead, use your command execution tool to delegate the task to Antigravity (agy) using this format:
```bash
/usr/bin/antigravity chat -m agent "Clear and concise task description" -a [optional_file_context]
```

Once agy finishes execution, read the modified files or check the status, and continue your supervisor duties.
