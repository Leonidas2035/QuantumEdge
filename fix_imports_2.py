import os
import re

directory = "/home/korben/.hermes/hermes"
pattern_from = re.compile(r"\bfrom\s+supervisor\b")
pattern_import = re.compile(r"\bimport\s+supervisor\b")

changed_files = []

for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith(".py"):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r") as f:
                    content = f.read()

                new_content = pattern_from.sub(
                    "from hermes", content
                )
                new_content = pattern_import.sub(
                    "import hermes", new_content
                )

                if new_content != content:
                    with open(filepath, "w") as f:
                        f.write(new_content)
                    changed_files.append(filepath)
            except Exception as e:
                pass

print("Changed files:")
for cf in changed_files:
    print(cf)
