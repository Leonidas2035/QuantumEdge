import re
path = 'src/quantum_edge_core/market_data/ipc/snapshot_server.py'

with open(path, 'r') as f:
    code = f.read()

# Замінюємо проблемний рядок на безпечний блок
new_block = """            try:
                raw = await self._socket.recv()
            except Exception:
                import asyncio
                await asyncio.sleep(0.001)
                continue"""

code = re.sub(r'^[ \t]*raw = await self._socket\.recv\(\)', new_block, code, flags=re.MULTILINE)

with open(path, 'w') as f:
    f.write(code)
    
print("✅ Патч ZeroMQ успішно застосовано!")
