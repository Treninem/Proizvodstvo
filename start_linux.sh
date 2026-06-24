#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/ensure_pdf_font.py --try-install
python3 scripts/live_start_check.py
python3 -m app.main
