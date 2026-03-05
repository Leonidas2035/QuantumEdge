import os
import re

directory = 'src/quantum_edge_core/supervisor'

changed_files = []

for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r') as f:
                content = f.read()

            new_content = re.sub(r'\bfrom\s+supervisor\b', 'from quantum_edge_core.supervisor', content)
            new_content = re.sub(r'\bimport\s+supervisor\b', 'import quantum_edge_core.supervisor', new_content)

            if new_content != content:
                with open(filepath, 'w') as f:
                    f.write(new_content)
                changed_files.append(filepath)

print("Changed files:")
for cf in changed_files:
    print(cf)
