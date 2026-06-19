from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    found = [p.relative_to(ROOT) for p in ROOT.rglob('*') if p.is_file() and p.name.lower().startswith('readme')]
    if found:
        raise SystemExit('Найден лишний README: ' + ', '.join(map(str, found)))
    print('OK')


if __name__ == '__main__':
    main()
