import os
from openai import OpenAI

class CodexClient:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not found")

        self.client = OpenAI(api_key=self.api_key)

    def send(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model="gpt-5.1-code-large",
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=30000,
            temperature=0
        )
        return response.choices[0].message.content
