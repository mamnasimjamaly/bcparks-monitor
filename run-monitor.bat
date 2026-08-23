@echo off
setlocal
cd /d "%~dp0app"

if not exist "%~dp0logs" mkdir "%~dp0logs"

set HEADLESS=1
set PYTHONIOENCODING=utf-8

"%~dp0venv\Scripts\python.exe" -u main.py >> "%~dp0logs\monitor.log" 2>&1
