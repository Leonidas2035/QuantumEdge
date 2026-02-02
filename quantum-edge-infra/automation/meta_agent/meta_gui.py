import os
import subprocess
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import yaml

from paths import BASE_DIR, PROMPTS_DIR, STAGES_PATH
from projects_config import load_project_registry


def slugify(text: str) -> str:
    """
    Simple slug for filenames: replace spaces, strip unsafe chars.
    """
    text = text.strip().replace(" ", "_")
    for ch in r'\/:*?"<>|':
        text = text.replace(ch, "")
    return text or "task"


def load_stages():
    if not os.path.exists(STAGES_PATH):
        return []
    with open(STAGES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    return data if isinstance(data, list) else []


def save_stages(stages):
    with open(STAGES_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            stages,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def on_add():
    task_name = entry_name.get().strip()
    task_body = text_prompt.get("1.0", tk.END).strip()
    project_id = project_var.get().strip()

    if not task_name:
        messagebox.showerror("Error", "Task name is required.")
        return
    if not task_body:
        messagebox.showerror("Error", "Task body cannot be empty.")
        return

    stages = load_stages()
    next_idx = len(stages) + 1
    slug = slugify(task_name)
    filename = f"stage_{next_idx:02d}_{slug}.md"

    os.makedirs(PROMPTS_DIR, exist_ok=True)
    md_path = os.path.join(PROMPTS_DIR, filename)

    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(task_body + "\n")

        rel_prompt_path = f"prompts/{filename}"
        stages.append({"name": task_name, "prompt": rel_prompt_path, "project": project_id})
        save_stages(stages)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to save task: {e}")
        return

    messagebox.showinfo("Success", f"Task added:\n  {filename}\nAdded to stages.yaml.")


def on_run_meta_agent():
    """
    Runs meta_agent.py once in a blocking call.
    """
    try:
        result = subprocess.run(
            ["python", "meta_agent.py"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            messagebox.showinfo("Meta-Agent", "Meta-Agent finished successfully.")
        else:
            messagebox.showerror("Meta-Agent", f"Meta-Agent failed.\n\n{result.stdout}\n{result.stderr}")
    except Exception as exc:
        messagebox.showerror("Meta-Agent", f"Failed to run Meta-Agent: {exc}")


def main():
    global entry_name, text_prompt, project_var

    root = tk.Tk()
    root.title("Meta-Agent GUI")
    root.geometry("700x500")

    registry = load_project_registry()
    project_var = tk.StringVar(value=registry.default_project_id)
    project_choices = sorted(list(registry.projects.keys()))

    frame_top = tk.Frame(root)
    frame_top.pack(fill=tk.X, padx=10, pady=10)

    lbl_name = tk.Label(frame_top, text="Task name/title:")
    lbl_name.pack(side=tk.LEFT)

    entry_name = tk.Entry(frame_top)
    entry_name.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

    lbl_project = tk.Label(frame_top, text="Project:")
    lbl_project.pack(side=tk.LEFT, padx=(10, 0))

    project_dropdown = ttk.Combobox(frame_top, textvariable=project_var, values=project_choices, width=20)
    project_dropdown.pack(side=tk.LEFT, padx=(5, 0))

    lbl_prompt = tk.Label(root, text="Task prompt / instructions (.md):")
    lbl_prompt.pack(anchor="w", padx=10)

    text_prompt = scrolledtext.ScrolledText(root, wrap=tk.WORD)
    text_prompt.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    frame_buttons = tk.Frame(root)
    frame_buttons.pack(pady=(0, 10))

    btn_add = tk.Button(frame_buttons, text="Add", command=on_add, width=12)
    btn_add.pack(side=tk.LEFT, padx=5)

    btn_run = tk.Button(frame_buttons, text="Start", command=on_run_meta_agent, width=12)
    btn_run.pack(side=tk.LEFT, padx=5)

    root.mainloop()


if __name__ == "__main__":
    main()
