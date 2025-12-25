import yaml
from codex_client import CodexClient
from prompt_builder import PromptBuilder
from file_manager import FileManager
from project_scanner import ProjectScanner

class MetaAgent:
    def __init__(self):
        self.client = CodexClient()
        self.builder = PromptBuilder()
        self.manager = FileManager()
        self.scanner = ProjectScanner()

        with open("stages.yaml", "r", encoding="utf-8") as f:
            self.stages = yaml.safe_load(f)

    def run(self):
        print("🚀 Meta-Agent Started\n")

        for stage in self.stages:
            name = stage["name"]
            prompt_file = stage["prompt"]

            print(f"=== Running stage: {name} ===")

            # load prompt instructions
            with open(prompt_file, "r", encoding="utf-8") as f:
                stage_instructions = f.read()

            # load project context (bot files)
            context = self.scanner.collect_project_files()

            # build Codex prompt
            full_prompt = self.builder.build_prompt(stage_instructions, context)

            print("📤 Sending prompt to Codex...")
            response = self.client.send(full_prompt)

            print("📥 Parsing Codex response...")
            self.manager.process_output(response)

            print(f"✅ Stage {name} completed\n")

        print("🎉 All stages completed!")

if __name__ == "__main__":
    agent = MetaAgent()
    agent.run()
