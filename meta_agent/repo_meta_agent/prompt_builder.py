class PromptBuilder:
    HEADER = (
        "You are Codex running inside Meta-Agent. "
        "Follow the task instructions strictly. "
        "Return files using the exact format below:\n"
        "===FILE: relative/or/absolute/path===\n"
        "<file content>\n"
        "Only include files that should be written.\n"
    )

    def build_prompt(self, stage_instructions: str, project_context: str) -> str:
        return (
            self.HEADER
            + "\n# Task Instructions\n"
            + stage_instructions
            + "\n# Project Context\n"
            + project_context
            + "\n# Output Guidance\n"
            + "Use the ===FILE: path=== blocks for any files to create or update. "
            + "Avoid extra commentary outside those blocks unless specifically requested.\n"
        )
