import sys
import os

# Ensure src/ is in sys.path
base = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(base, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Add other necessary paths for the tools to work
sys.path.append(os.path.join(src_path, "quantum_edge_infra"))

from quantum_edge_infra.tools import mcp_server

if __name__ == "__main__":
    try:
        print(mcp_server.list_project_structure())
    except Exception as e:
        print(e)
