from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    reporting_source = (ROOT / "app/services/reporting.py").read_text(encoding="utf-8")
    handlers_source = (ROOT / "app/handlers/reports.py").read_text(encoding="utf-8")

    assert "class PdfUnavailableError" in reporting_source
    assert "raise PdfUnavailableError(PDF_UNAVAILABLE_TEXT)" in reporting_source
    assert "PDF сейчас недоступен. Отправил отчёт в Excel." in handlers_source

    forbidden_public_fragments = [
        "PDF не собран",
        "start_linux.sh",
        "fonts-dejavu-core",
        "REPORT_PDF_FONT",
        "NotoSans-Regular.ttf",
    ]
    for fragment in forbidden_public_fragments:
        assert fragment not in handlers_source, fragment

    print("OK")


if __name__ == "__main__":
    main()
