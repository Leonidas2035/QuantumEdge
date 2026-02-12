import socket
import os

orig_connect = socket.socket.connect

def new_connect(self, address):
    host = address[0]
    # Allow localhost and local IPC/sockets
    if isinstance(host, str) and host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        print(f"\n[SECURITY] Blocked connection attempt to {host}")
        raise RuntimeError(f"Real network access attempted to {host} in offline mode")
    return orig_connect(self, address)

socket.socket.connect = new_connect
print("Network blocker active (Subprocess)")
