# Step 84 — релиз 84a и финальная проверка производства

Дата: 2026-08-13.
Репозиторий: `Treninem/Proizvodstvo`.

## Текущая исходная версия

Продолжать только с текущего `main`. Не откатываться к Step82/Step83 runtime.

- Bot: `84`
- Backend build: `84a`
- Mini App: `20260813a`
- Активный JavaScript: `webapp/static/app-20260813a.js`
- Активный CSS: `webapp/static/style-20260812a.css`
- Docker image label: `84a-mini-20260813a`
- Tenant architecture: `tenant-isolation v2`
- Production DB: `/app/data/production_account.sqlite3`
- Public runtime domain: `https://procontrol.bothost.tech`

Активный JavaScript содержит собственный идентификатор релиза:

```js
// Mini App release: 20260813a
const MINI_APP_VERSION="20260813a";
```

Telegram-кнопка Mini App использует `MINI_UI_VERSION = "20260813a"`. Скрытый OWNER-экран версии показывает Bot 84 / Backend 84a / Mini App 20260813a.

## Что исправлено на Step84

### 1. Кнопка «Ещё» в мобильном Mini App

Найден реальный UX-дефект: нижняя кнопка `Ещё` имела `data-tab="more"`, но страницы `page-more` не существовало. Общий обработчик делал `showTab("more")`, текущая страница скрывалась и пользователь получал пустой экран.

Исправлено: `more` обрабатывается до общего `showTab`, раскрывает дополнительную панель `.tabs.mobile-open`, синхронизирует active state и `aria-expanded`.

### 2. Постоянный UI wiring audit

Добавлен `scripts/ui_wiring_audit_step84.py`, который постоянно проверяет:

- `data-tab` → существующая страница или явно разрешённый special-tab;
- статические `data-action` → JavaScript handler;
- статические `byId(...)` → существующий DOM id;
- обязательную мобильную навигацию `Производство / Склад / План / Отчёты / Ещё`;
- специальный рабочий обработчик `Ещё`.

Аудит сам определяет активный versioned JavaScript из `index.html`, поэтому не привязан к номеру релиза.

### 3. Cache-safe Mini App release

Предыдущий versioned asset `app-20260812g.js` нельзя было безопасно изменять на месте, потому что versioned static отдаётся с длительным immutable cache.

Исправлено правильно: создан новый asset `app-20260813a.js`, а `index.html` переключён на новое имя. Старый клиентский cache не может удержать исправленную сборку под старым URL.

### 4. Согласованная версия во всех точках

Версия синхронизирована в:

- `webapp/server.py` → `MINI_UI_VERSION=20260813a`, build `84a`;
- `webapp/static/index.html` → `app-20260813a.js`;
- `webapp/static/app-20260813a.js` → `MINI_APP_VERSION=20260813a`;
- `webapp/static/app.js` → byte-identical alias активного JS;
- `app/keyboards.py` → Telegram Mini App URL с `20260813a`;
- `app/handlers/owner.py` → OWNER-only версия Bot 84 / Backend 84a / Mini App 20260813a;
- `Dockerfile` → image label `84a-mini-20260813a`.

### 5. Очистка репозитория

Удалены одноразовые patch/release-файлы, временные workflows и старые исполняемые frontend runtime-сборки. В `webapp/static` после очистки остаются только актуальные runtime-файлы и aliases:

- `app-20260813a.js`
- `app.js`
- `style-20260812a.css`
- `style.css`
- `index.html`
- `manifest.webmanifest`
- `img/`

`app.js` byte-identical `app-20260813a.js`, `style.css` byte-identical `style-20260812a.css`.

### 6. Python Ruff F полностью очищен

После функционального аудита удалены только реально неиспользуемые imports/locals (`F401/F841`). Одноразовый cleanup-workflow после выполнения удалён.

Постоянный CI требует:

```text
ruff check app webapp --select F
```

без исключений и без скрытого игнорирования unused/undefined/redefined проблем.

### 7. Проверяется реальный production Docker image

Добавлен `scripts/docker_contract_audit_step84.py`. Он выводит release identity из `webapp/server.py`/`index.html` и проверяет Dockerfile:

- image label соответствует `84a / 20260813a`;
- `COPY . ./` копирует весь source и активный Mini App asset;
- открыт порт 3000;
- перед runtime выполняется `scripts/live_start_check.py`;
- контейнер запускает `exec python -m app.runtime`.

Главный CI теперь дополнительно выполняет настоящий:

```text
docker build --tag proizvodstvo-step84-ci .
```

на Dockerfile с `python:3.11-slim`, после чего запускает `live_start_check.py` уже **внутри собранного production image** с синтетическими CI-секретами. Build и preflight прошли SUCCESS.

## Финальный source + Docker audit

Финальный полный GitHub Actions run:

- Run: `31685682982`
- Head: `a995b71a2902dcd6851a6827a4ecb59aebf7f7ff`
- Conclusion: `SUCCESS`

Все рабочие этапы прошли:

1. Dependency install + `pip check`
2. Python compile
3. 798 static SQL queries against current SQLite schema
4. Полный Ruff `F` correctness gate
5. Docker release contract audit
6. **Production Docker image build on Python 3.11**
7. **Startup preflight inside built Docker image**
8. Deep runtime API/select/frontend alias audit
9. Exhaustive all-method API smoke
10. Active Mini App JS syntax
11. UI wiring audit
12. Architecture + tenant audit
13. Unit regressions
14. Legacy QA scripts
15. Security/UI text/final/flow/smoke audits
16. Runtime configuration guard
17. Workflow completion checks

Exhaustive API smoke проверяет все 123 API operations на заполненной изолированной test DB; необработанных HTTP 500 нет.

## Постоянный live deployment gate

Файлы:

- `scripts/live_deployment_gate_step84.py`
- `.github/workflows/live-deployment-gate-step84.yml`

Gate проверяет реальный Bothost не только по HTTP 200, а по точному runtime:

- `/health` = 200 и `build=84a`;
- `/ready` = 200, `database=true`, `build=84a`;
- `/mini` = 200 без redirect;
- `X-Mini-App-Version=20260813a`;
- HTML ссылается на `app-20260813a.js`;
- versioned JS/CSS и aliases совпадают с GitHub по SHA-256;
- manifest совпадает по SHA-256;
- `/api/accounts?user_id=1` без авторизации = 403.

Workflow запускается по push и по расписанию, поэтому stale deployment обнаруживается автоматически.

## Реальный Bothost — текущий внешний блокер

Live gate run `31683164892`, 30 попыток, завершился `FAILURE`.

Во всех попытках Bothost стабильно отдавал старый runtime:

- `/health` отвечает, но `build` отсутствует;
- `/ready` отвечает, но `build` отсутствует;
- `/mini` отдаёт `X-Mini-App-Version=20260812f`;
- live HTML не ссылается на `app-20260813a.js`;
- `/static/app-20260813a.js` = HTTP 404;
- live `app.js`, `style.css`, `manifest.webmanifest` имеют другие SHA-256.

Это не browser cache и не проблема сборки source: текущий production Docker image `84a/20260813a` реально собирается и проходит preflight в GitHub Actions, а live-сервер Bothost физически не содержит нового versioned asset.

## Единственный обязательный следующий live-шаг

В панели Bothost для производственного бота нужно выполнить полноценный **Deploy / пересборку из текущей ветки `main`**, а не только Restart старого контейнера. Одновременно проверить, что подключён именно репозиторий `Treninem/Proizvodstvo` и ветка `main`.

После deploy релиз считается live только при одновременном выполнении:

- `/health` → `200`, `build=84a`;
- `/ready` → `200`, `database=true`, `build=84a`;
- `/mini` → `200`, без redirect, `X-Mini-App-Version=20260813a`;
- HTML → `app-20260813a.js`;
- `/static/app-20260813a.js` → `200` и точный SHA GitHub;
- aliases/manifest совпадают по SHA;
- unauthenticated `/api/accounts` → `403`.

После ручного deploy достаточно повторно запустить GitHub workflow `Live Step84 deployment gate`; source-аудит повторять с нуля не требуется, если код не менялся.

## Security

Реальные `.env`, BOT_TOKEN, MINIAPP_API_TOKEN, BACKUP_ENCRYPTION_KEY, production SQLite, backups, logs и пользовательские выгрузки в GitHub не коммитить.

Исторически публиковавшиеся значения `MINIAPP_API_TOKEN` и `BACKUP_ENCRYPTION_KEY` считать скомпрометированными. Если они ещё не были заменены в Bothost, после стабильного deploy их нужно ротировать в переменных окружения хостинга.
