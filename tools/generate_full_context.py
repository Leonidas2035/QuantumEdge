import os
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================

# Whitelisted extensions (Code, Config, Docs)
WHITELIST_EXTENSIONS = {
    # Code
    ".py",
    ".sh",
    ".js",
    # Config
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".env.template",
    # Docs
    ".md",
    ".txt",
}

# Directories to strictly exclude
BLACKLIST_DIR_NAMES = {
    "venv",
    ".git",
    ".vscode",
    "__pycache__",
    "node_modules",
    "site-packages",
    "logs",
    "data",
    "db",
    "artifacts",
    "run",
}

# Specific relative paths to exclude
BLACKLIST_PATHS = {"tests/fixtures"}

OUTPUT_FILE = "FULL_SYSTEM_CONTEXT_V2.txt"

# ==========================================
# LOGIC
# ==========================================


def is_whitelisted_file(file_path):
    """Check if file has a whitelisted extension."""
    # Special handle for specific filenames if needed (e.g. .env.template has
    # a leading dot but is checked as extension by suffix usually? No,
    # suffix is .template)
    # pathlib suffix for .env.template is .template.
    # But user listed .env.template explicitly.
    if file_path.name == ".env.template":
        return True
    return file_path.suffix.lower() in WHITELIST_EXTENSIONS


def should_exclude_dir(dir_name, root_path):
    """Check if directory is blacklisted by name or path."""
    if dir_name in BLACKLIST_DIR_NAMES:
        return True

    full_path = root_path / dir_name
    try:
        # Assuming script runs from root, relative to CWD
        rel_path = full_path.relative_to(Path.cwd())
        # Normalize separators
        rel_str = str(rel_path).replace(os.sep, "/")
        if rel_str in BLACKLIST_PATHS:
            return True
    except ValueError:
        pass

    return False


def generate_tree(start_path):
    """Generates a visual ASCII tree of INCLUDED files only."""
    tree_lines = []

    def walk(current_path, prefix=""):
        try:
            items = sorted(os.listdir(current_path))
        except OSError:
            return

        # Filter items to only those we care about
        dirs = []
        files = []
        for item in items:
            p = current_path / item
            if p.is_dir():
                if not should_exclude_dir(item, current_path):
                    dirs.append(item)
            elif p.is_file() and is_whitelisted_file(p):
                files.append(item)

        all_items = sorted(dirs + files)

        for i, item in enumerate(all_items):
            is_last = i == len(all_items) - 1
            p = current_path / item

            connector = "└── " if is_last else "├── "
            tree_lines.append(f"{prefix}{connector}{item}")

            if p.is_dir():
                extension = "    " if is_last else "│   "
                walk(p, prefix + extension)

    tree_lines.append(".")  # Root
    walk(start_path)
    return "\n".join(tree_lines)


def collect_files(start_path):
    """Walks the directory and collects all whitelisted files."""
    collected = []
    for root, dirs, files in os.walk(start_path):
        # Modify dirs in-place to skip blacklisted ones
        dirs[:] = [d for d in dirs if not should_exclude_dir(d, Path(root))]

        for f in sorted(files):
            p = Path(root) / f
            if is_whitelisted_file(p):
                collected.append(p)
    return collected


def main():
    root_dir = Path.cwd()
    output_path = root_dir / OUTPUT_FILE

    print(f"Scanning project from: {root_dir}")
    print("Generating Visual Tree...")
    tree_content = generate_tree(root_dir)

    print("Collecting files...")
    files = collect_files(root_dir)
    print(f"Found {len(files)} included files.")

    print(f"Writing to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("PROJECT DIRECTORY TREE\n")
        f.write("======================\n")
        f.write(tree_content)
        f.write("\n\n")

        for file_path in files:
            try:
                rel_path = file_path.relative_to(root_dir)
                f.write(f"<<<<<<<<<<<<<<<<<<<< FILE: {rel_path} >>>>>>>>>>>>>>>>>>>>\n")

                with open(file_path, "r", encoding="utf-8") as infile:
                    content = infile.read()
                    f.write(content)

                f.write("\n<<<<<<<<<<<<<<<<<<<< END FILE >>>>>>>>>>>>>>>>>>>>\n\n")
            except UnicodeDecodeError:
                f.write("[BINARY OR NON-UTF8 CONTENT SKIPPED]\n")
                f.write("\n<<<<<<<<<<<<<<<<<<<< END FILE >>>>>>>>>>>>>>>>>>>>\n\n")
            except Exception as e:
                f.write(f"[ERROR READING FILE: {e}]\n")
                f.write("\n<<<<<<<<<<<<<<<<<<<< END FILE >>>>>>>>>>>>>>>>>>>>\n\n")

    print("SUCCESS: Full Context Generated.")


if __name__ == "__main__":
    main()
