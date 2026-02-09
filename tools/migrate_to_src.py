#!/usr/bin/env python3
"""
tools/migrate_to_src.py

Automates the migration of QuantumEdge to a src-layout structure.
Handles directory creation, file moves, and cleanup safely.
"""

import shutil
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def safe_create_dir(path: Path):
    """Creates a directory if it doesn't exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        logger.info(f"CREATED: {path.relative_to(PROJECT_ROOT)}")
    else:
        logger.debug(f"EXISTS: {path.relative_to(PROJECT_ROOT)}")


def safe_move_contents(src: Path, dest: Path):
    """Moves all contents from src to dest."""
    if not src.exists():
        logger.warning(
            f"SKIPPING: Source directory not found: {src.relative_to(PROJECT_ROOT)}"
        )
        return

    safe_create_dir(dest)

    for item in src.iterdir():
        dest_path = dest / item.name
        if dest_path.exists():
            logger.warning(
                f"SKIPPING: Destination exists: {dest_path.relative_to(PROJECT_ROOT)}"
            )
            continue

        try:
            shutil.move(str(item), str(dest_path))
            logger.info(
                f"MOVED: {item.relative_to(PROJECT_ROOT)} -> "
                f"{dest_path.relative_to(PROJECT_ROOT)}"
            )
        except Exception as e:
            logger.error(f"ERROR: Could not move {item}: {e}")


def smart_move_file(filename: str, potential_sources: list[Path], dest: Path):
    """Finds a file in potential sources and moves it to dest."""
    found = False
    for src in potential_sources:
        src_file = src / filename
        if src_file.exists():
            if dest.exists():
                logger.warning(
                    f"SKIPPING: Destination exists for {filename}: "
                    f"{dest.relative_to(PROJECT_ROOT)}"
                )
                found = True  # Treat as found to stop searching
                break

            safe_create_dir(dest.parent)
            try:
                shutil.move(str(src_file), str(dest))
                logger.info(
                    f"MOVED: {src_file.relative_to(PROJECT_ROOT)} -> "
                    f"{dest.relative_to(PROJECT_ROOT)}"
                )
                found = True
                break
            except Exception as e:
                logger.error(f"ERROR: Could not move {src_file}: {e}")
                return

    if not found:
        # Fallback: Search explicitly in the entire project if not found in expected locations
        # Excluding common ignore dirs to speed up
        logger.info(
            f"SEARCHING: {filename} not found in expected paths, searching project..."
        )
        for path in PROJECT_ROOT.rglob(filename):
            if any(
                part in [".git", "venv", ".venv", "build", "dist", "__pycache__"]
                for part in path.parts
            ):
                continue

            if dest.exists():
                logger.warning(
                    f"SKIPPING: Destination exists for {filename}: "
                    f"{dest.relative_to(PROJECT_ROOT)}"
                )
                break

            safe_create_dir(dest.parent)
            try:
                shutil.move(str(path), str(dest))
                logger.info(
                    f"MOVED: {path.relative_to(PROJECT_ROOT)} -> "
                    f"{dest.relative_to(PROJECT_ROOT)}"
                )
                break
            except Exception as e:
                logger.error(f"ERROR: Could not move {path}: {e}")
        else:
            logger.warning(f"NOT FOUND: Could not locate {filename} anywhere.")


def cleanup_empty_dir(path: Path):
    """Removes a directory if it is empty."""
    if path.exists() and path.is_dir() and not any(path.iterdir()):
        try:
            path.rmdir()
            logger.info(f"REMOVED EMPTY DIR: {path.relative_to(PROJECT_ROOT)}")
        except Exception as e:
            logger.error(f"ERROR: Could not remove directory {path}: {e}")


def main():
    logger.info("STARTING: Migration to src-layout")

    # 1. Create Directory Structure
    src_dir = PROJECT_ROOT / "src"
    scripts_dir = PROJECT_ROOT / "scripts"
    tests_dir = PROJECT_ROOT / "tests"

    safe_create_dir(src_dir)
    safe_create_dir(scripts_dir)
    safe_create_dir(tests_dir)
    safe_create_dir(src_dir / "quantum_edge_core")
    safe_create_dir(src_dir / "quantum_edge_ml")
    safe_create_dir(src_dir / "quantum_edge_infra")

    # 2. Move Logic
    logger.info("--- Moving Package Contents ---")
    safe_move_contents(
        PROJECT_ROOT / "quantum-edge-core", src_dir / "quantum_edge_core"
    )
    safe_move_contents(PROJECT_ROOT / "quantum-edge-ml", src_dir / "quantum_edge_ml")
    safe_move_contents(
        PROJECT_ROOT / "quantum-edge-infra", src_dir / "quantum_edge_infra"
    )

    # 3. Smart File Search & Move
    logger.info("--- Moving Entry Points ---")

    # Move QuantumEdge.py -> scripts/run_orchestrator.py
    smart_move_file(
        "QuantumEdge.py", [PROJECT_ROOT], scripts_dir / "run_orchestrator.py"
    )

    # Move run_bot.py -> scripts/run_bot.py
    # Previously found at: quantum-edge-core/strategies/scalper_v1/run_bot.py OR bot/run_bot.py
    smart_move_file(
        "run_bot.py",
        [
            PROJECT_ROOT / "bot",
            PROJECT_ROOT / "quantum-edge-core" / "strategies" / "scalper_v1",
            PROJECT_ROOT / "quantum-edge-core" / "bot",
            PROJECT_ROOT / "strategies" / "scalper_v1",
        ],
        scripts_dir / "run_bot.py",
    )

    # 4. Cleanup
    logger.info("--- Cleaning Up ---")
    cleanup_empty_dir(PROJECT_ROOT / "quantum-edge-core")
    cleanup_empty_dir(PROJECT_ROOT / "quantum-edge-ml")
    cleanup_empty_dir(PROJECT_ROOT / "quantum-edge-infra")
    cleanup_empty_dir(PROJECT_ROOT / "bot")

    logger.info("COMPLETED: Migration finished.")


if __name__ == "__main__":
    main()
