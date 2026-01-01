@echo off
REM Запуск ISCC (Inno Setup Compiler). Шлях до ISCC має бути в PATH або вкажи повний шлях.
REM Приклад якщо Inno встановлено у дефолтну папку:
REM "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ai_scalper_bot.iss

if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ai_scalper_bot.iss
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
  "C:\Program Files\Inno Setup 6\ISCC.exe" ai_scalper_bot.iss
) else (
  echo ISCC not found. Please install Inno Setup and ensure ISCC.exe is in default path.
  pause
)
