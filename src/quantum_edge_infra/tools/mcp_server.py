from mcp.server.fastmcp import FastMCP
import subprocess
import os
from typing import Dict

# Ініціалізація сервера
mcp = FastMCP("QuantumEdge Orchestrator")

# --- CATEGORY 1: DATA SKILLS (QuestDB) ---

@mcp.tool()
def query_market_data(symbol: str, limit: int = 100) -> str:
    """
    Виконує SQL-запит до локальної QuestDB для отримання останніх тіків.
    Використовувати для аналізу ринкової ситуації перед написанням стратегії.
    """
    # Емуляція запиту (тут буде реальний http request до QuestDB API:9000)
    query = f"SELECT * FROM trades WHERE symbol = '{symbol}' LIMIT {limit};"
    # TODO: Реалізувати реальний запит через requests/psycopg2
    return f"Executed Query: {query}. [Mock Data: Price=98000, Vol=1.5]"

# --- CATEGORY 2: DEVOPS SKILLS (GKE/Docker) ---

@mcp.tool()
def check_container_logs(service_name: str, lines: int = 50) -> str:
    """
    Читає останні логи вказаного сервісу (MarketDataHub, Bot) через Docker/Kubectl.
    Корисно для дебагу помилок.
    """
    try:
        # Безпечний виклик команди
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), service_name],
            capture_output=True, text=True
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        return f"Error reading logs: {e!s}"

# --- CATEGORY 3: CODING SKILLS (Safety Wrappers) ---

@mcp.tool()
def list_project_structure() -> str:
    """
    Повертає дерево файлів проєкту, ігноруючи __pycache__ та .git.
    Допомагає агенту зрозуміти, де лежать конфіги.
    """
    structure = []
    for root, dirs, files in os.walk("."):
        if ".git" in root or "__pycache__" in root:
            continue
        level = root.replace(".", "").count(os.sep)
        indent = " " * 4 * (level)
        structure.append(f"{indent}{os.path.basename(root)}/")
        subindent = " " * 4 * (level + 1)
        for f in files:
            structure.append(f"{subindent}{f}")
    return "\n".join(structure)

@mcp.tool()
def run_tests(test_path: str = "tests/") -> str:
    """
    Запускає pytest для вказаного модуля.
    Агент ПОВИНЕН викликати це після зміни коду.
    """
    try:
        result = subprocess.run(
            ["pytest", test_path], capture_output=True, text=True
        )
        return result.stdout
    except Exception as e:
        return f"Test Execution Failed: {e!s}"

# --- CATEGORY 4: TRADING CONTEXT ---

@mcp.tool()
def get_risk_status() -> Dict:
    """
    Повертає поточні метрики ризику з SupervisorAgent.
    Critical для перевірки 'Kill Switch'.
    """
    # Тут можна читати спільний файл стану або робити запит до API Supervisor
    return {
        "status": "ACTIVE",
        "current_drawdown_pct": 1.2,
        "open_positions": 2,
        "max_allowed_drawdown": 4.0
    }

if __name__ == "__main__":
    mcp.run()