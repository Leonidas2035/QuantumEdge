import os
import shutil

# 1. Створення цільової директорії
DOCS_DIR = os.path.join("docs", "Documentation")
os.makedirs(DOCS_DIR, exist_ok=True)

# 2. Список розширень, які треба перемістити
TARGET_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

# 3. Винятки (файли, які НЕ МОЖНА чіпати)
EXCLUDE_FILES = {
    "README.md",
    "AGENTS.md",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-runtime.txt",
    "FULL_SYSTEM_CONTEXT_V3.txt",  # Ваш новий файл
    "cleanup_project.py",  # Цей скрипт
}


def move_docs():
    print(f"Починаю переміщення документації в {DOCS_DIR}...")
    moved_count = 0

    # Скануємо тільки корінь (щоб не ламати вкладені модулі коду)
    for filename in os.listdir("."):
        if not os.path.isfile(filename):
            continue

        # Перевірка на винятки
        if filename in EXCLUDE_FILES:
            continue

        # Перевірка розширення
        _, ext = os.path.splitext(filename)
        if ext.lower() in TARGET_EXTENSIONS:
            source = filename
            destination = os.path.join(DOCS_DIR, filename)

            try:
                shutil.move(source, destination)
                print(f"✅ Переміщено: {filename}")
                moved_count += 1
            except Exception as e:
                print(f"❌ Помилка з {filename}: {e}")

    print(f"\nЗавершено. Переміщено файлів: {moved_count}")


def update_agents_md():
    print("\nОновлюю AGENTS.md...")
    rule_text = """
## 7. DOCUMENTATION MAINTENANCE RULE
Strict Rule: Any code modification, module refactoring, or architectural change MUST be accompanied by an update to the corresponding documentation. If a module changes, its specific documentation in `docs/Documentation` must be revised. The `FULL_SYSTEM_CONTEXT` file must be kept in sync with the file structure.
"""
    if os.path.exists("AGENTS.md"):
        with open("AGENTS.md", "a", encoding="utf-8") as f:
            f.write(rule_text)
        print("✅ Правило додано в AGENTS.md")
    else:
        print("⚠️ Файл AGENTS.md не знайдено!")


if __name__ == "__main__":
    move_docs()
    update_agents_md()
