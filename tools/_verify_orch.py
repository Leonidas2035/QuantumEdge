import ast
p = '/home/layman/QuantumEdge/QuantumEdge.py'
ast.parse(open(p).read())
print('OK', p)
