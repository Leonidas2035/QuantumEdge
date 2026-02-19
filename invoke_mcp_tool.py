from quantum_edge_infra.tools import mcp_server

if __name__ == "__main__":
    try:
        print(mcp_server.list_project_structure())
    except Exception as e:
        print(e)
