import os
import re

class FileManager:
    FILE_PATTERN = r"===FILE:\s*(.*?)===\n(.*?)(?=\n===FILE:|$)"

    def process_output(self, response: str):
        matches = re.findall(self.FILE_PATTERN, response, flags=re.S|re.M)
        for path, code in matches:
            full_path = os.path.join("output", path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"📝 Created: {full_path}")
