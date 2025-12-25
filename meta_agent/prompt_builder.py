class PromptBuilder:
    HEADER = (
        "You are Codex running inside Meta-Agent. "
        "Follow the task instructions strictly. "
        "Return files using the exact format below:\n"
        "===FILE: relative/or/absolute/path===\n"
        "<file content>\n"
        "Only include files that should be written.\n"
    )

    def build_prompt(self, stage_instructions: str, project_context: str = "", metadata: dict | None = None) -> str:
        sections = [self.HEADER]

        if metadata:
            meta_lines = "\n".join(f"{key}: {value}" for key, value in metadata.items())
            sections.append("# Task Metadata\n" + meta_lines)

        sections.append("# Task Instructions\n" + stage_instructions.strip())

        if project_context:
            sections.append("# Project Context\n" + project_context.strip())

        sections.append(
            "# Output Guidance\n"
            "Use the ===FILE: path=== blocks for any files to create or update. "
            "Avoid extra commentary outside those blocks unless specifically requested."
        )

        return "\n\n".join(sections) + "\n"
