import ast, os
# 1) supervisor_market_analysis.py syntax
p = '/home/layman/QuantumEdge/tools/supervisor_market_analysis.py'
ast.parse(open(p).read())
print('OK', p)
# 2) firecrawl.key present & non-empty
k = '/home/layman/QuantumEdge/config/firecrawl.key'
print('key file:', 'OK' if os.path.exists(k) and os.path.getsize(k) > 0 else 'MISSING/EMPTY')
