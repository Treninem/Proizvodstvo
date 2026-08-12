from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '20260812a'


def main() -> None:
    index = (ROOT / 'webapp/static/index.html').read_text(encoding='utf-8')
    keyboards = (ROOT / 'app/keyboards.py').read_text(encoding='utf-8')
    app_js = ROOT / f'webapp/static/app-{VERSION}.js'
    style = ROOT / f'webapp/static/style-{VERSION}.css'

    assert app_js.is_file(), app_js
    assert style.is_file(), style
    assert f'app-{VERSION}.js' in index, 'index.html не использует текущий JS'
    assert f'style-{VERSION}.css' in index, 'index.html не использует текущий CSS'
    assert f'MINI_UI_VERSION = "{VERSION}"' in keyboards
    assert 'app.js?v=78' not in index and 'style.css?v=78' not in index

    # Системное меню не должно возвращаться в обычное меню пользователя.
    assert 'Группы' not in str(__import__('app.keyboards', fromlist=['main_menu']).main_menu().model_dump())
    print('ui_version_audit OK')


if __name__ == '__main__':
    main()
