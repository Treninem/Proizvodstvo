@echo off
cd /d %~dp0
python scripts\live_start_check.py
if errorlevel 1 pause && exit /b 1
python -m app.main
pause
