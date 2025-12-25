# ============================================================
# build_runbot_exe.ps1 — Build console run_bot.exe
# ============================================================

Set-Location "C:\ai_scalper_bot"

# Activate venv
& "C:\ai_scalper_bot\venv\Scripts\activate.bat"

# Install PyInstaller if needed
pip install pyinstaller

# Create run_bot.py launcher
$py = @"
import subprocess
import os

base = r"C:\ai_scalper_bot"
py = os.path.join(base, "venv", "Scripts", "python.exe")
cmd = [py, "-m", "bot.market_data.ws_manager"]

subprocess.call(cmd)
"@

Set-Content -Path "run_bot.py" -Value $py -Encoding UTF8

# Build run_bot.exe
pyinstaller `
    --onefile `
    --console `
    --icon=bot_icon.ico `
    --name=run_bot.exe `
    run_bot.py

Write-Host "Build complete. run_bot.exe is in dist folder."
