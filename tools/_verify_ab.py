import ast
files = [
    '/home/layman/QuantumEdge/QuantumEdge.py',
    '/home/layman/QuantumEdge/tools/supervisor_market_analysis.py',
    '/home/layman/QuantumEdge/tools/_verify_b_monitor.py',
]
for p in files:
    ast.parse(open(p).read())
    print('OK', p)
