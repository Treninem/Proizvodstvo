from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    index = (ROOT / 'webapp/static/index.html').read_text(encoding='utf-8')
    keyboards = (ROOT / 'app/keyboards.py').read_text(encoding='utf-8')

    js_match = re.search(r'app-(20\d{6}[a-z])\.js', index)
    css_match = re.search(r'style-(20\d{6}[a-z])\.css', index)
    assert js_match, 'index.html не содержит versioned JS'
    assert css_match, 'index.html не содержит versioned CSS'
    js_version = js_match.group(1)
    css_version = css_match.group(1)

    app_js = ROOT / f'webapp/static/app-{js_version}.js'
    style = ROOT / f'webapp/static/style-{css_version}.css'
    assert app_js.is_file(), app_js
    assert style.is_file(), style
    assert f'app-{js_version}.js' in index, 'index.html не использует активный JS'
    assert f'style-{css_version}.css' in index, 'index.html не использует активный CSS'
    assert f'MINI_UI_VERSION = "{js_version}"' in keyboards, 'кнопки бота используют другую Mini App версию'
    assert f'const MINI_APP_VERSION="{js_version}"' in app_js.read_text(encoding='utf-8'), 'JS сообщает неверную версию'
    assert 'app.js?v=78' not in index and 'style.css?v=78' not in index

    # Системное меню не должно возвращаться в обычное меню пользователя.
    from app.keyboards import main_menu
    assert 'Группы' not in str(main_menu().model_dump())
    print(f'ui_version_audit OK js={js_version} css={css_version}')


if __name__ == '__main__':
    main()
