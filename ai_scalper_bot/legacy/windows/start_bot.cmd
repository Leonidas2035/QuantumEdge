@echo off
REM launcher for ai_scalper_bot
cd /d "%~dp0"
call "%~dp0venv\Scripts\activate.bat"
REM run in interactive window - change as needed
echo Activating venv and printing python version...
python -V
echo To start bot run: python run_bot.py
pause
