@echo off
cd /d %~dp0
python scripts\clean_runtime_artifacts.py
python -m compileall app scripts
python scripts\smoke_test.py
python scripts\flow_test.py
python scripts\security_audit.py
python scripts\report_flow_test.py
python scripts\report_file_type_test.py
python scripts\report_export_quality_test.py
python scripts\report_targets_step38_test.py
python scripts\exact_entity_match_step39_test.py
python scripts\custom_plan_step40_test.py
python scripts\owner_test_mode_step41_test.py
python scripts\clean_runtime_artifacts.py
python scripts\ui_text_audit.py
python scripts\final_audit.py
pause
