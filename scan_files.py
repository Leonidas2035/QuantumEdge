import os
import json

directories = ['MarketDataHub', 'SupervisorAgent', 'ai_scalper_bot']
files_to_scan = []

for d in directories:
    if os.path.exists(d):
        for root, _, files in os.walk(d):
            for file in files:
                if file.endswith('.py') or file.endswith('.yaml'):
                    files_to_scan.append(os.path.join(root, file))

print(f"Found {len(files_to_scan)} files.")
with open('files_to_scan.json', 'w') as f:
    json.dump(files_to_scan, f)
