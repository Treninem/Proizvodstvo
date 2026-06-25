#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/clean_runtime_artifacts.py
python3 scripts/ensure_pdf_font.py --try-install --strict
python3 -m compileall app scripts
python3 scripts/smoke_test.py
python3 scripts/flow_test.py
python3 scripts/security_audit.py
python3 scripts/report_flow_test.py
python3 scripts/report_file_type_test.py
python3 scripts/help_button_test.py
python3 scripts/report_export_quality_test.py
python3 scripts/report_targets_step38_test.py
python3 scripts/exact_entity_match_step39_test.py
python3 scripts/custom_plan_step40_test.py
python3 scripts/compact_chat_report_step44_test.py
python3 scripts/multi_report_step48_test.py
python3 scripts/group_silence_step49_test.py
python3 scripts/group_single_selection_step50_test.py
python3 scripts/owner_test_mode_step41_test.py
python3 scripts/owner_foreign_account_step42_test.py
python3 scripts/private_group_setup_access_step45_test.py
python3 scripts/job_assignment_flow_test.py
python3 scripts/job_assignment_private_job_step46_test.py
python3 scripts/clean_runtime_artifacts.py
python3 scripts/ui_text_audit.py
python3 scripts/final_audit.py
