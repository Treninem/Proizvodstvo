@echo off
cd /d %~dp0
python scripts\clean_runtime_artifacts.py
python -m compileall app scripts
python scripts\smoke_test.py
python scripts\flow_test.py
python scripts\security_audit.py
python scripts\clean_runtime_artifacts.py
python scripts\ui_text_audit.py
python scripts\final_audit.py
pause
