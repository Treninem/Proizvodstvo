# Step85 — bot + Mini App recovery and cache-safe release

Дата: 2026-08-16

## Итог

Репозиторий приведён в согласованное и проверенное состояние после повреждённого промежуточного hotfix-коммита.

Текущий runtime-контракт:

- Bot: 84
- Backend build: 85
- Mini App: 20260816a
- Active JS: `webapp/static/app-20260816a.js`
- Alias JS: `webapp/static/app.js` — байт-в-байт совпадает с активным asset
- Docker label: `85-mini-20260816a`
- Architecture: tenant-isolation v2

## Что исправлено

1. Восстановлен `app/handlers/owner.py` из последнего проверенного Step84 после обнаружения, что ошибочный hotfix дописал HTML Mini App внутрь Python-модуля бота.
2. Исправлен критический Mini App bootstrap: обработчик `entityCodeType` ссылался на отсутствующую функцию `updateEntityCodeEntities`, что могло останавливать дальнейшую инициализацию JavaScript.
3. Выпущен новый cache-safe asset `app-20260816a.js`, чтобы Telegram WebView не оставался на старом `immutable` JS из кэша.
4. Синхронизированы версии Backend / Mini App / Telegram-кнопки / Docker / owner version output / deployment tests.
5. Добавлен runtime safety repair `app/services/frontend_runtime.py` как дополнительная защита от повторного пропуска entity-code initializer.
6. Добавлены регрессионные тесты:
   - `tests/test_frontend_runtime_repair.py`
   - `tests/test_step85_release_contract.py`
7. Одноразовый workflow миграции удалён после успешного применения.

## Проверка

GitHub Actions run `31913645388` — SUCCESS.

Успешно прошли:

- dependency install / pip check
- Python compile
- SQL schema compile audit
- Ruff F correctness
- Docker deployment contract
- production Docker build
- production-image startup preflight
- deep runtime API + frontend alias audit
- exhaustive all-method API smoke
- active Mini App JavaScript syntax check
- Mini App UI wiring audit
- architecture + tenant isolation audit
- unit regressions
- existing QA scripts
- security + final audits
- runtime configuration guard

Кодовый контрольный commit после релиза и постоянных regression tests: `1e929f3df261e2c10d9ac26cb0fbda3314d50080`.

## Важно для следующей работы

Не возвращать `app-20260813a.js` как активный immutable asset и не повторять незавершённый `84b/20260813b` hotfix. Все дальнейшие изменения начинать от текущего `main` и после каждого этапа прогонять полный GitHub Actions audit.
