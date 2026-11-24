import ast
src = open('/home/layman/QuantumEdge/tools/supervisor_action_bc.py').read()
tree = ast.parse(src)
print('syntax OK')
for needle in ['async def main', 'cancel_all_orders', 'create_order', 'positionSide',
               'fetch_positions', 'fetch_open_orders', 'set_sandbox_mode']:
    print(('FOUND   ' if needle in src else 'MISSING '), needle)
