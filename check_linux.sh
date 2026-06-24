#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/clean_runtime_artifacts.py
python3 -m compileall app scripts
python3 scripts/smoke_test.py
python3 scripts/flow_test.py
python3 scripts/security_audit.py
python3 scripts/report_flow_test.py
python3 scripts/report_file_type_test.py
python3 scripts/report_export_quality_test.py
python3 scripts/report_targets_step38_test.py
python3 scripts/exact_entity_match_step39_test.py
python3 scripts/clean_runtime_artifacts.py
python3 scripts/ui_text_audit.py
python3 scripts/final_audit.py
