import ast
for p in ['tools/supervisor_market_analysis.py']:
    ast.parse(open('/home/layman/QuantumEdge/' + p).read())
    print(p, 'syntax OK')
