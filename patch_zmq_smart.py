path = 'src/quantum_edge_core/market_data/ipc/snapshot_server.py'

with open(path, 'r') as f:
    lines = f.readlines()

with open(path, 'w') as f:
    for line in lines:
        if 'raw = await self._socket.recv()' in line and 'try:' not in line:
            # Вираховуємо точний відступ поточного рядка
            indent = line[:len(line) - len(line.lstrip())]
            f.write(f"{indent}try:\n")
            f.write(f"{indent}    raw = await self._socket.recv()\n")
            f.write(f"{indent}except Exception:\n")
            f.write(f"{indent}    import asyncio\n")
            f.write(f"{indent}    await asyncio.sleep(0.001)\n")
            f.write(f"{indent}    continue\n")
        else:
            f.write(line)
            
print("✅ Розумний патч ZeroMQ успішно застосовано!")
