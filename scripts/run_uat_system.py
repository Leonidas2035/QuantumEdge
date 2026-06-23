#!/usr/bin/env python3
"""
QuantumEdge UAT Orchestrator
Запускає Hub, Bot та Supervisor в єдиному терміналі з префіксами логів.
"""

import subprocess
import sys
import os
import signal
import threading

# Команди для запуску наших трьох китів
PROCESSES = {
    "HUB": [sys.executable, "-m", "quantum_edge_core.market_data.hub"],
    "BOT": [sys.executable, "src/quantum_edge_core/ai_scalper_bot/run_bot.py"],
    "SUP": [
        sys.executable,
        "/home/korben/.hermes/hermes/supervisor.py",
        "run-foreground",
    ],  # Перевір точний шлях
}

colors = {
    "HUB": "\033[94m",  # Blue
    "BOT": "\033[92m",  # Green
    "SUP": "\033[95m",  # Magenta
    "RESET": "\033[0m",
}

active_processes = []


def stream_reader(pipe, prefix):
    """Читає потік процесу і виводить його з кольоровим префіксом."""
    color = colors.get(prefix, colors["RESET"])
    for line in iter(pipe.readline, ""):
        sys.stdout.write(f"{color}[{prefix}]{colors['RESET']} {line}")
        sys.stdout.flush()


def terminate_all(sig, frame):
    print("\n🛑 Зупинка системи UAT...")
    for p in active_processes:
        p.terminate()
    sys.exit(0)


def main():
    print("🚀 Запуск QuantumEdge End-to-End UAT...")

    # Налаштовуємо PYTHONPATH: додаємо src та корінь проекту
    env = os.environ.copy()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_dir = os.path.join(project_root, "src")

    current_pythonpath = env.get("PYTHONPATH", "")
    new_path = f"{src_dir}:{project_root}"
    env["PYTHONPATH"] = (
        f"{new_path}:{current_pythonpath}" if current_pythonpath else new_path
    )

    # Load .env file explicitly if exists so subprocesses get GOOGLE_API_KEY
    env_file = os.path.join(project_root, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")

    # Alternatively accept the user environment var
    if "GOOGLE_API_KEY" not in env and "GOOGLE_API_KEY" in os.environ:
        env["GOOGLE_API_KEY"] = os.environ["GOOGLE_API_KEY"]

    signal.signal(signal.SIGINT, terminate_all)

    # Запускаємо процеси
    for name, cmd in PROCESSES.items():
        print(f"[*] Стартує {name}...")
        p = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Об'єднуємо помилки з виводом
            text=True,
            bufsize=1,
        )
        active_processes.append(p)

        # Запускаємо окремий потік для читання логів цього процесу
        t = threading.Thread(target=stream_reader, args=(p.stdout, name), daemon=True)
        t.start()

    print("✅ Усі системи запущені. Натисніть Ctrl+C для зупинки.\n" + "-" * 50)

    # Чекаємо завершення
    for p in active_processes:
        p.wait()


if __name__ == "__main__":
    main()
