class PromptBuilder:
    HEADER = """
ТИ — GPT-5.1-Codex, експерт з Python, ML, HFT, торгових систем.
Пиши ПОВНІ файли у форматі:

===FILE: path/to/file===
<код>

Ніколи не скорочуй код.
Не додавай пояснень поза файлами.
"""

    def build_prompt(self, stage_instructions: str, project_context: str) -> str:
        return (
            self.HEADER +
            "\n# Вимоги етапу:\n" +
            stage_instructions +
            "\n# Існуючий код проєкту:\n" +
            project_context +
            "\n# Згенеруй нові або оновлені файли нижче:\n"
        )
