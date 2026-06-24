from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import reporting


def _try_install_dejavu() -> bool:
    if os.name != "posix":
        return False
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    if not shutil.which("apt-get"):
        return False
    commands = [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "fontconfig", "fonts-dejavu-core"],
    ]
    for command in commands:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return False
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--try-install", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    ok, message = reporting.pdf_font_status()
    if ok:
        print(message)
        return

    if args.try_install and _try_install_dejavu():
        ok, message = reporting.pdf_font_status()
        if ok:
            print(message)
            return

    print(message)
    print("PDF включится после установки fonts-dejavu-core или после указания REPORT_PDF_FONT в .env.")
    if args.strict:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
