from google import genai
import os

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

print("Доступні моделі для вашого ключа:")
print("-" * 30)
for m in client.models.list():
    if "generateContent" in m.supported_actions:
        print(f"ID: {m.name:35} | Display: {m.display_name}")
