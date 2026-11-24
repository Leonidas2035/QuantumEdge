#!/usr/bin/env python3
import os
import sys
import time
from google import genai
from google.genai import types

from jules_tools import JulesLocalTools

script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(script_dir, "../../"))
tools_inst = JulesLocalTools(repo_root)


def throttle():
    time.sleep(3.0)


# Інструменти з логуванням
def search_code(query: str) -> str:
    print(f"  [Tool] Шукаю код: {query}...")
    throttle()
    return tools_inst.search_code(query)


def read_file(path: str) -> str:
    print(f"  [Tool] Читаю файл: {path}...")
    throttle()
    return tools_inst.read_file(path)


def write_file(path: str, content: str) -> str:
    print(f"  [Tool] Записую у файл: {path}...")
    throttle()
    return tools_inst.write_file(path, content)


def check_syntax(path: str) -> str:
    print(f"  [Tool] Перевірка синтаксису: {path}...")
    throttle()
    return tools_inst.check_syntax(path)


jules_tools = [search_code, read_file, write_file, check_syntax]


# --- СИСТЕМА КЕШУВАННЯ КОНТЕКСТУ ---
def load_project_cache():
    cache_path = os.path.join(repo_root, ".jules_context_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_content = f.read()
            print("  [Cache] Локальний контекст проєкту успішно завантажено.")
            return f"\n\n--- ПОТОЧНИЙ КОНТЕКСТ ПРОЄКТУ ---\n{cache_content}\n----------------------------------\n"
        except Exception as e:
            print(f"  [Cache] Помилка читання: {e}")
    return ""


base_instruction = "Ти — Jules, Lead Architect HFT-системи QuantumEdge. Використовуй інструменти для аналізу коду."
system_instruction = base_instruction + load_project_cache()
# -----------------------------------


def run_jules_agent(user_query: str) -> str:
    try:
        client = genai.Client()
    except Exception as e:
        return f"[!] Помилка клієнта: {e}"

    models_priority = [
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
    ]

    for model_name in models_priority:
        try:
            print(f"[*] Використовую {model_name}...")
            chat = client.chats.create(
                model=model_name,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=jules_tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=False
                    ),
                    temperature=0.1,
                ),
            )

            response = chat.send_message(user_query)

            if response.text:
                usage = response.usage_metadata
                if usage:
                    print(
                        f"\n[Витрати токенів]: Вхідні: {usage.prompt_token_count} | Вихідні: {usage.candidates_token_count}"
                    )
                return response.text
            else:
                return "[?] Модель виконала дії, але не надала текстового звіту."

        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"[!] {model_name} обмежена квотою. Fallback...")
                continue
            return f"[!] Помилка: {e}"

    return "[!] Немає доступних моделей."


if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = run_jules_agent(query)
        print("\n[Жуль каже]:\n", result)
    else:
        print("Використання: jules 'запит'")
