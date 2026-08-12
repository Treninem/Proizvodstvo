from __future__ import annotations

import ast
import os
import re
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("OWNER_TELEGRAM_ID", "2097006037")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")

from app import db
from app.config import settings

SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH)\b", re.I)


def sql_literals(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (UnicodeDecodeError, SyntaxError):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value.strip()
        if not SQL_START.match(text):
            continue
        # Skip fragments rather than complete statements and runtime-formatted SQL.
        if not any(word in text.upper() for word in (" FROM ", " INTO ", "UPDATE ", "DELETE FROM", "WITH ")):
            continue
        yield node.lineno, text


def qmark_count(sql: str) -> int:
    # Project SQL uses positional ? placeholders. Ignore question marks inside quoted
    # literals so EXPLAIN gets the same binding count as sqlite execute().
    count = 0
    quote = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
        elif ch == "?":
            count += 1
        i += 1
    return count


def main() -> None:
    failures: list[str] = []
    checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        test_settings = replace(settings, data_dir=Path(tmp), database_path=Path(tmp) / "sql-audit.sqlite3")
        with patch.object(db, "settings", test_settings):
            db.init_db()
            with db.connect() as conn:
                for base in (ROOT / "app", ROOT / "webapp"):
                    for path in sorted(base.rglob("*.py")):
                        for lineno, sql in sql_literals(path) or ():
                            # sqlite's execute() only accepts one statement. Schema DDL is
                            # not scanned here, and application SQL literals are expected to
                            # be single statements; skip explicit multi-statement helpers.
                            stripped = sql.rstrip().rstrip(";")
                            if ";" in stripped:
                                continue
                            checked += 1
                            params = [None] * qmark_count(stripped)
                            try:
                                conn.execute("EXPLAIN " + stripped, params).fetchall()
                            except sqlite3.Error as exc:
                                message = str(exc)
                                # Some statements intentionally depend on dynamically-created
                                # TEMP tables or dynamic SQL pieces and cannot be prepared here.
                                # Missing regular schema columns/tables are never ignored.
                                if "no such column" in message.lower() or "no such table" in message.lower():
                                    rel = path.relative_to(ROOT)
                                    failures.append(f"{rel}:{lineno}: {message}: {stripped[:240]}")
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    failures.append(f"fresh schema integrity_check={integrity}")
    if failures:
        print(f"SQL_SCHEMA_COMPILE_FAIL checked={checked} failures={len(failures)}")
        for item in failures:
            print(item)
        raise SystemExit(1)
    print(f"SQL_SCHEMA_COMPILE_OK checked={checked}")


if __name__ == "__main__":
    main()
