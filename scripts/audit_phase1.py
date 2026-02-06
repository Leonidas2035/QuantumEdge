
import os
import sys
import importlib.util
from pathlib import Path
import tempfile

def check_step(name, status, message=""):
    status_str = "[OK]" if status else "[FAIL]"
    print(f"{status_str} {name:.<40} {message}")

def check_dir(path):
    return Path(path).is_dir()

def check_file(path):
    return Path(path).is_file()

def check_import(module_name):
    return importlib.util.find_spec(module_name) is not None

def check_logging():
    try:
        # Import setup function
        # Ensure src is in pythonpath
        src_path = Path("src").resolve()
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        
        from quantum_edge_core.logging_setup import setup_logging
        import structlog
        import logging

        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        
        # Configure logging to file (simulate by adding handler manually or just capturing stdout if we could, 
        # but the requirement says "Write a test log to a temporary file using quantum_edge_core.logging_setup")
        # Since setup_logging() directs to stdout/stderr by default, we need to inspect it or wrap it.
        # Actually, standard structlog setup usually goes to stdout. 
        # But we can verify that we can GET a logger and log without error.
        # To meet "Verify the file was created" requirement, we might need a file handler or redirect stdout.
        # Let's verify we can capture the log.
        
        # Redirect stdout to a file for this test
        original_stdout = sys.stdout
        with open(tmp_path, 'w') as f:
            sys.stdout = f
            setup_logging()
            logger = structlog.get_logger()
            logger.info("AUDIT_TEST_LOG")
        sys.stdout = original_stdout

        # Read file
        content = tmp_path.read_text()
        tmp_path.unlink()
        
        return "AUDIT_TEST_LOG" in content
    except Exception as e:
        print(f"Logging check failed: {e}")
        return False

def main():
    print("Running Phase 1 Audit...\n")
    
    # 1. Structure Check
    check_step("Structure: src/quantum_edge_core", check_dir("src/quantum_edge_core"))
    check_step("Structure: src/quantum_edge_infra", check_dir("src/quantum_edge_infra"))
    check_step("Structure: src/quantum_edge_ml", check_dir("src/quantum_edge_ml"))
    
    # 2. Config Check
    check_step("Config: pyproject.toml", check_file("pyproject.toml"))
    
    # 3. Library Check
    check_step("Library: structlog", check_import("structlog"))
    check_step("Library: msgspec", check_import("msgspec"))
    check_step("Library: uvloop", check_import("uvloop"))
    
    # 4. Logging Check
    check_step("Logging: System Integration", check_logging())
    
    # 5. Documentation Check
    check_step("Docs: phase1_summary.md", check_file("docs/phase1_summary.md"))

if __name__ == "__main__":
    main()
