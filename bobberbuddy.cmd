@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Set up the virtual environment using the commands in README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" calibrate.py
if errorlevel 1 pause
