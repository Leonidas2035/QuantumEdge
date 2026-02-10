# ROLE

You are **Codex**, running inside the **Meta-Agent project at `C:/meta_agent`**.

In THIS STAGE your job is to:

1. Perform a **lightweight but focused audit** of the Meta-Agent codebase.
2. Produce **concise technical documentation** for developers and operators.

You MUST work **within strict context/size limits** to avoid API errors:
- use **only the `meta_agent` project** as context (no ai_scalper_bot, no Supervisor agent),
- keep the total code context you read/summarize within **~200k characters**,
- avoid inlining large pieces of code in the output (refer to modules and functions by name, summarize behavior).

You MUST NOT modify any existing code or configs in this stage.  
Only create documentation/report files.

---

# GOALS

By the end of this stage you must:

1. Produce an **audit report** of the Meta-Agent architecture & quality.
2. Produce a **short architecture overview** document.
3. Produce a **module-level technical reference**.
4. Produce a **short runbook** on how to use Meta-Agent + GUI (and multi-project support, if available).

All outputs must be placed under the `output/` directory.

---

# SCOPE

Focus your analysis on the Meta-Agent itself:

- `meta_agent.py` – main runner / orchestrator.
- `meta_gui.py` – GUI for adding/running tasks.
- `codex_client.py` – OpenAI client wrapper.
- `project_scanner.py` / `projects_config.py` – multi-project and context loading logic.
- `file_manager.py` – how `===FILE: ...===` blocks are parsed and written.
- `task_schema.py`, `task_manager.py`, `report_schema.py`, `supervisor_runner.py`, `offmarket_*` – if they exist, treat them as part of the orchestration core.

You do **NOT** need to open or analyze:

- `C:\ai_scalper_bot`,
- `C:\Supervisor agent`,
- large data/log/output directories.

If such paths exist in config, **ignore them for this stage**.

---

# TASKS

## 1. Code audit (read-only)

Perform a read-only audit of the Meta-Agent codebase with focus on:

- **Architecture & responsibilities**:
  - how `meta_agent.py` coordinates tasks/stages,
  - how GUI integrates (Add/Start buttons → stages.yaml → meta_agent.py),
  - how project selection & context loading works (project_scanner, projects_config),
  - how file changes / reports are written (file_manager, output/).

- **Correctness & robustness**:
  - obvious bugs / fragile spots,
  - error handling (network errors, missing files, bad config),
  - potential performance/size issues with context loading (but stay inside ~200k chars).

- **Safety & boundaries** (high-level):
  - whether it’s easy випадково перезаписати важливі файли,
  - чи є захист від занадто великих промптів,
  - чи відокремлені Meta-Agent файли від бойового бота.

Summarize findings, but **do not change any code**.

## 2. Technical documentation

Create three concise docs in `output/`:

1. `output/meta_agent_audit_report.md`  
   Content:
   - Overview of what you audited.
   - Strengths (what is already good).
   - Issues & risks, grouped by module.
     - For кожної проблеми: severity (low/medium/high), impact, short suggested fix.
   - Recommended priorities (“must fix”, “should fix”, “nice to have”).

2. `output/meta_agent_architecture.md`  
   Content:
   - High-level architecture diagram in text form (sections, bullets).
   - Key components:
     - runner (meta_agent),
     - GUI,
     - project scanning,
     - task/report layer,
     - supervisor/off-market (if present).
   - Data flow:
     - GUI → stages.yaml → meta_agent → project_scanner + codex_client → file_manager → output/.
   - How multi-project support works (if implemented).

3. `output/meta_agent_technical_reference.md`  
   Content:
   - Short per-module reference:
     - module name → main classes/functions + їх роль.
   - Important configuration files and directories:
     - `stages.yaml`, `config/...`, `prompts/`, `output/`, etc.
   - Where API keys / env settings are expected (без конкретних секретів).

4. `output/meta_agent_runbook.md`  
   Content:
   - “How to use Meta-Agent” for a developer/operator:
     - як створити задачу через GUI (Add),
     - як запустити виконання (Start),
     - що відбувається з `stages.yaml` і `prompts/` після виконання,
     - де шукати результати (`output/`),
     - як вибирається проект (meta_agent / ai_scalper_bot / Supervisor agent), якщо така логіка вже є.
   - Basic troubleshooting:
     - типові помилки (відсутній ключ, quota, занадто великий промпт),
     - де дивитись логи.

---

# OUTPUT FORMAT

You MUST output **exactly four file blocks**, and nothing else:

```text
===FILE: output/meta_agent_audit_report.md===
<content>

===FILE: output/meta_agent_architecture.md===
<content>

===FILE: output/meta_agent_technical_reference.md===
<content>

===FILE: output/meta_agent_runbook.md===
<content>
Constraints:

Do NOT output any other ===FILE: ...=== blocks.

Do NOT modify existing files (no code, no configs), only create/overwrite the four docs above.

Keep each document reasonably short (target: up to ~20–25k characters), avoid dumping large code snippets.

Summaries, lists, and explanations are preferred over raw code.

CONSTRAINTS
Context:

Use ONLY the Meta-Agent project (C:/meta_agent) as context.

Respect an internal limit of ~200k chars of code/comments you read/serialize into your own working memory.

No changes to code or configuration files.

No operations with git or external tools.

Do not read or reference any secrets or .env contents; you may mention that API keys are loaded from env, but not their values.
