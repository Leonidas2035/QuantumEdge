# ROLE

You are **Codex**, running inside the **Meta-Agent project at `C:/meta_agent`**.

In THIS STAGE your job is to **redesign the project context loader (`project_scanner`) and Meta-Agent orchestration** so that:

- context size sent to the LLM is **hard-limited** (e.g. 200–300k chars),
- heavy/irrelevant directories are **skipped** (data, logs, output, reports, .git, __pycache__, etc.),
- Meta-Agent can work with **multiple projects/repositories**:
  - `C:\ai_scalper_bot`
  - `C:\meta_agent`
  - `C:\Supervisor agent`
- each stage/task can explicitly say **which project** it is about (ai_scalper_bot / meta_agent / supervisor_agent),
- Meta-Agent uses the **correct repo** for each stage and does **not** pull ai_scalper_bot when the task is only about Meta-Agent or Supervisor.

This is an infrastructure / architecture stage. You are allowed to change Meta-Agent code, but not the trading logic itself.

---

# GOAL OF THIS STAGE

After this stage Meta-Agent must:

1. Use a **configurable project registry** instead of a single hardcoded project root.
2. Use an improved **project_scanner** that:
   - collects only relevant files,
   - enforces a global `max_chars` limit for context,
   - skips heavy and noisy directories by default.
3. For each stage (from `stages.yaml`), pick the **right project root**:
   - `ai_scalper_bot` when task is about the trading bot,
   - `meta_agent` when task is about Meta-Agent itself,
   - `supervisor_agent` when task is about the Supervisor project.
4. Avoid hitting OpenAI limits (error 400 / invalid_request_error due to oversized prompts).

---

# SCOPE

You may modify / create:

- `project_scanner.py` – main context collector.
- `meta_agent.py` – to choose project per stage and pass options to project_scanner.
- A small **project registry config** (e.g. `projects.yaml` or `projects_config.py`).
- Any small helper functions needed to wire this together.

You must **not**:

- Touch secrets / env encryption logic (`env_crypto.py`, `.env`, keys).
- Change trading logic in `../ai_scalper_bot` or `C:/Supervisor agent` beyond normal file reading.
- Implement supervisor logic itself (only project selection and context scanning).

---

# FUNCTIONAL REQUIREMENTS

## 1. Multi-project registry

Introduce a simple project registry that maps **project IDs** to **paths**.

You can choose either:

### Variant A — `config/projects.yaml` (preferred)

Create a YAML file, e.g. `config/projects.yaml`:

```yaml
default: ai_scalper_bot

projects:
  ai_scalper_bot:
    path: ../ai_scalper_bot
  meta_agent:
    path: .
  supervisor_agent:
    path: ../Supervisor agent
Paths are relative to C:/meta_agent (BASE_DIR).

Then create a small module, e.g. projects_config.py, with:

python
Копіювати код
from dataclasses import dataclass
from typing import Dict
import os
import yaml

@dataclass
class ProjectInfo:
    project_id: str
    root_path: str  # absolute path

@dataclass
class ProjectRegistry:
    default_project_id: str
    projects: Dict[str, ProjectInfo]

def load_project_registry(config_path: str = DEFAULT_PROJECTS_YAML) -> ProjectRegistry:
    """
    Reads config/projects.yaml, resolves relative paths to absolute (based on BASE_DIR),
    validates that project ids are unique and paths exist (or at least are plausible).
    """
Variant B — projects_config.py with inline dict
Якщо YAML не хочеш — можна зробити простий Python-словник у projects_config.py з тими ж даними. Важливо, щоб:

було одне місце, де визначено, що таке ai_scalper_bot, meta_agent, supervisor_agent,

решта коду користується саме цим реєстром.

2. Selecting project per stage (stages.yaml support)
Розшир stages.yaml, щоб кожен етап може мати поле project:

yaml
Копіювати код
- name: Meta-Agent GUI self-audit
  project: meta_agent
  prompt: prompts/stage_01_Meta-Agent_GUI_self-audit.md

- name: Upgrade scalper bot execution
  project: ai_scalper_bot
  prompt: prompts/stage_02_Upgrade_execution.md
Правила:

project необов’язкове поле (для backward compatibility).

Якщо project відсутнє → використовуємо default з config/projects.yaml (найчастіше ai_scalper_bot).

Значення project має відповідати одному з ключів у реєстрі (ai_scalper_bot, meta_agent, supervisor_agent).

У meta_agent.py:

при завантаженні stages.yaml для кожного етапу зчитуй project (або підставляй default),

перед запуском стадії обирай project_root через ProjectRegistry,

передавай цей project_root у project_scanner.

3. Improved project_scanner (context limits & skipping heavy dirs)
Перепроєктуй project_scanner.py так, щоб у ньому була центральна функція:

python
Копіювати код
def collect_project_context(
    project_root: str,
    max_chars: int = 250_000,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
) -> str:
    """
    Walks the project_root and collects a textual snapshot of important files
    (code + configs + docs) into a single string, limited to max_chars.
    """
Вимоги:

Ігнорувати важкі/шумні директорії за замовчуванням:

.git

.github

.venv, venv, env

__pycache__

data

logs

output

reports

будь-які тимчасові або кеш-папки (якщо їх легко розпізнати).

Зроби дефолтний список DEFAULT_EXCLUDE_DIRS всередині модуля.

Враховувати тільки релевантні файли:

Основні розширення: .py, .md, .yaml, .yml, .toml, .json, можливо .txt.

Ігнорувати великі двійкові файли, архіви, зображення, тощо.

Можна зробити DEFAULT_INCLUDE_EXTS = {".py", ".md", ".yaml", ".yml", ".toml", ".json"}.

Ліміт за розміром (max_chars):

Під час обходу директорій додавай файли по черзі, поки сума символів не перевищить max_chars.

Коли ліміт майже досягнутий — або перестати додавати нові файли, або обрізати останній файл (але краще просто зупинитись).

Додай короткий заголовок-коментар у контексті, наприклад:

text
Копіювати код
### FILE: bot/core/execution.py
<file content>
У meta_agent можна логувати, скільки файлів/символів реально потрапило в контекст.

Обмеження на розмір одиночного файлу:

Якщо файл дуже великий (наприклад > 100k символів) — можна:

або пропустити його повністю,

або взяти тільки початок/кінець (але це вже опціонально).

Сформулюй просту, прозору політику (і задокументуй це в docstring).

4. Integration in meta_agent.py
Онови meta_agent.py так, щоб:

На старті:

завантажити ProjectRegistry через projects_config.load_project_registry(...).

мати константу MAX_CONTEXT_CHARS (наприклад 250_000) — можна зробити як конфігurable.

Для кожного етапу:

отримати project_id зі stages.yaml (або default),

знайти project_root в registry,

викликати collect_project_context(project_root, max_chars=MAX_CONTEXT_CHARS, ...),

додати цей контекст до промпта.

Логувати ключову інформацію:

який project_id обрано для стадії,

який project_root,

скільки символів контексту було зібрано (і скільки файлів).

Якщо project_id невідомий (немає в registry):

записати зрозумілий лог/помилку,

можна:

або впасти з error (краще),

або використати default project (як fallback) і зазначити це в логах.

CONSTRAINTS / DO NOT
Не чіпати логіку шифрування/секретів (env_crypto.py, .env, ключі).

Не впроваджувати нові зовнішні залежності (крім yaml, якщо його вже немає — але швидше за все він уже є).

Не міняти формат stages.yaml несумісно:

додавання поля project має бути необов’язковим, старі файли мають працювати.

Не читати/не торкатись великих data/logs/output/reports, .git і т.п. у контексті.

OUTPUT REQUIREMENTS
In your response, provide full files, not diffs.

Mandatory:

text
Копіювати код
===FILE: project_scanner.py===
<updated full content with max_chars, exclude dirs, etc.>

===FILE: projects_config.py===
<new module or updated full content if it already exists>

===FILE: meta_agent.py===
<updated full content with project selection per stage and limited context size>
If you create config/projects.yaml, output it as well:

text
Копіювати код
===FILE: config/projects.yaml===
<YAML content>
No diff/patch syntax. Each file block must be a complete file ready to be written to disk.
