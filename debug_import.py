import sys
import os

print(f"PYTHONPATH: {os.environ.get('PYTHONPATH')}")
print(f"sys.path: {sys.path}")

try:
    import bot
    print(f"bot: {bot}")
    print(f"bot file: {bot.__file__}")
except ImportError as e:
    print(f"ImportError bot: {e}")

try:
    import bot.ml
    print(f"bot.ml: {bot.ml}")
except ImportError as e:
    print(f"ImportError bot.ml: {e}")
