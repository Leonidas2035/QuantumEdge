import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'quantum-edge-infra', 'tools'))
try:
    import mcp_server
except ImportError:
    sys.path.append(os.getcwd())
    from tools import mcp_server

if __name__ == "__main__":
    try:
        print(mcp_server.list_project_structure())
    except Exception as e:
        print(e)
