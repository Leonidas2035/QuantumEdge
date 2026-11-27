"""AST-based Zero-Duplication Guards for the active QuantumEdge Hybrid contour."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_DIRS = (
    REPO_ROOT / "src" / "quantum_edge_core" / "contracts",
    REPO_ROOT / "src" / "quantum_edge_core" / "execution",
    REPO_ROOT / "src" / "quantum_edge_core" / "orchestrator",
    REPO_ROOT / "src" / "quantum_edge_core" / "market_data",
    REPO_ROOT / "src" / "quantum_edge_ml" / "model_router",
    REPO_ROOT / "src" / "quantum_edge_ml" / "forecasting",
)

ACTIVE_FILES = (
    REPO_ROOT / "tools" / "qe_harness_cli.py",
    REPO_ROOT / "tools" / "agy_openai_proxy.py",
)


def _iter_active_python_files() -> list[Path]:
    files: list[Path] = []
    for directory in ACTIVE_DIRS:
        if directory.is_dir():
            for py_file in sorted(directory.rglob("*.py")):
                if "tests" not in py_file.parts:
                    files.append(py_file)
    for py_file in ACTIVE_FILES:
        if py_file.is_file():
            files.append(py_file)
    return files


def _parse_ast(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_active_contour_files_discovered() -> None:
    files = _iter_active_python_files()
    assert len(files) >= 10


def test_ccxt_bingx_only_in_bingx_vst_adapter() -> None:
    allowed = (REPO_ROOT / "src" / "quantum_edge_core" / "execution" / "bingx_vst_adapter.py").resolve()
    violations: list[str] = []

    for path in _iter_active_python_files():
        tree = _parse_ast(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "ccxt"
                and node.attr == "bingx"
                and path.resolve() != allowed
            ):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not violations, f"ccxt.bingx found outside bingx_vst_adapter.py: {violations}"


def test_bingx_live_feed_only_in_market_data_feed() -> None:
    allowed = {
        (REPO_ROOT / "src" / "quantum_edge_core" / "market_data" / "feeds" / "bingx.py").resolve(),
        (REPO_ROOT / "src" / "quantum_edge_core" / "market_data" / "hub.py").resolve(),
    }
    violations: list[str] = []

    for path in _iter_active_python_files():
        tree = _parse_ast(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "BingXLiveFeed":
                if path.resolve() not in allowed:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not violations, f"BingXLiveFeed defined outside allowed modules: {violations}"


def test_risk_guard_and_executor_classes_only_in_canonical_execution() -> None:
    allowed = {
        (REPO_ROOT / "src" / "quantum_edge_core" / "execution" / "risk_guard.py").resolve(),
        (REPO_ROOT / "src" / "quantum_edge_core" / "execution" / "lean_executor.py").resolve(),
    }
    violations: list[str] = []

    for path in _iter_active_python_files():
        tree = _parse_ast(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (
                node.name.endswith("RiskGuard") or node.name.endswith("Executor")
            ):
                if path.resolve() not in allowed:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} ({node.name})")

    assert not violations, f"Duplicate RiskGuard/Executor classes found: {violations}"


def test_no_legacy_generativeai_or_explicit_cached_contents() -> None:
    genai_violations: list[str] = []
    cache_violations: list[str] = []

    for path in _iter_active_python_files():
        tree = _parse_ast(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "generativeai" in alias.name:
                        genai_violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "generativeai" in mod or any("generativeai" in a.name for a in node.names):
                    genai_violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in {"cachedContents", "cached_contents"}:
                cache_violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "cachedContents" in node.value:
                cache_violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not genai_violations, f"Deprecated google.generativeai imports found: {genai_violations}"
    assert not cache_violations, f"Explicit cachedContents usage found: {cache_violations}"
