# Step 83 — финальный полный системный аудит

Дата: 2026-08-13.
Репозиторий: `Treninem/Proizvodstvo`.
Активный Mini App source: `20260812g`.
Backend build marker: `83i`.
Постоянный CI revision: `83m-all-api-methods`.

## Что проверяется теперь постоянно

Проверка больше не опирается на ручное обнаружение отдельных пустых экранов.

- Python compile.
- Активный Mini App JavaScript через `node --check`.
- Ruff correctness: undefined/redefined names (`F821/F822/F823/F811`) считаются ошибкой.
- 798 статических SQL-запросов компилируются против свежей реальной SQLite-схемы.
- 99 UI actions, 141 `<select>`, 286 DOM references.
- Из 141 select 110 динамические; для всех 110 подтверждён путь заполнения.
- 123 API routes проверяются на авторизацию/маршрутизацию.
- 32 GET API реально вызываются на заполненной изолированной test DB без HTTP 500.
- Все 123 API operations (`GET/POST/PUT/PATCH/DELETE`) реально вызываются через FastAPI TestClient на изолированной test DB; ни одной HTTP 500/необработанного исключения.
- Статусы exhaustive smoke: `200=46`, `400=48`, `403=7`, `404=17`, `422=5`, `500=0`.
- 69 таблиц с `chat_id`: 62 tenant business tables + 7 явно классифицированных routing/transient.
- Tenant split-scope migration и двухфирменная изоляция.
- Cross-tenant SQL JOIN audit: `candidates=0`.
- Повторные top-level Python definitions и duplicate API routes запрещены постоянным аудитом.
- Полный seeded E2E: площадки, участки, отделы, места хранения, сотрудники, должности, комплектующие, сырьё, складские позиции, изделия, счётчики, план, операции, оборудование, партии, задания, качество, пополнение, ТО, передачи, company structure и Mini App bootstrap.
- Вторая фирма в E2E не может видеть данные первой и наоборот.
- Legacy QA, reports, security, UI, final, flow, smoke и runtime guard.

Последний полный постоянный run: GitHub Actions `31645575095` — SUCCESS.

## Ошибки, найденные проактивно в полном аудите

### Step83j — shadowed definitions

Найдено 15 повторно определённых top-level Python функций. Python молча использовал только последнюю реализацию, поэтому исправление в старой копии могло вообще не работать.

- `repository.py`: права текущего учёта, места хранения, должности.
- `reporting.py`: отчёты/Excel/PDF; необходимая legacy-реализация сохранена под явным именем `_legacy_report_sections_step63`.
- `dashboard.py`: формирование dashboard.

После безопасной очистки: duplicate definitions = 0, duplicate API routes = 0.
Commit: `6ed39c569439ecab6c841c7d8ee6d2f175cafdc8`.

### Step83k — hidden runtime NameError

Ruff нашёл две реальные ветки, которые прежние сценарии не выполняли:

1. `app/keyboards.py`: `component_choice_keyboard()` вызывал несуществующий `format_amount`.
2. `app/services/stock_risk.py`: `_event_accessible_to_user()` использовал неопределённый `scope` вместо переданного `chat_id`.

Обе ошибки исправлены и проверены runtime-тестами и полным QA.
Commit: `ac769daed23f8025ef1366070c24b1e7d21c49d3`.

### Step83l — stale frontend aliases

Найден риск запуска старой Mini App через legacy/PWA пути:

- `manifest.webmanifest` всё ещё открывал `/mini?v=20260809b`;
- `webapp/static/app.js` содержал старый `app-20260809b.js`;
- `webapp/static/style.css` был старым alias.

Исправлено:

- manifest `start_url=/mini`;
- `app.js` byte-identical текущему `app-20260812g.js`;
- `style.css` byte-identical текущему `style-20260812a.css`;
- постоянный deep runtime audit теперь проверяет эти aliases.

Verified source commit: `8e3bfd73e94f4bdb1a53b5dd5112183400e045c3`.

### Step83m — exhaustive API smoke

Добавлен `scripts/api_all_methods_smoke_step83.py` и включён в постоянный CI.

На заполненной изолированной базе реально вызываются все 123 API operations. Результат последнего run:

- 46 × HTTP 200;
- 48 × HTTP 400 (ожидаемая бизнес-валидация на synthetic data);
- 7 × HTTP 403 (ожидаемые права);
- 17 × HTTP 404 (synthetic/nonexistent object IDs);
- 5 × HTTP 422 (request validation);
- 0 × HTTP 500;
- 0 необработанных исключений.

Permanent CI commit: `0409fcfd48ca70153716d381d6262e56d53d6359`.

## Live Bothost — проверка после полного source audit

Live probe GitHub Actions run `31645393790` показал:

- `/health` = HTTP 200;
- `/ready` = HTTP 200, database=true;
- `/mini` = HTTP 200, без redirect;
- `/api/accounts?user_id=1` без Telegram/служебной авторизации = HTTP 403;
- live HTML всё ещё ссылается на `app-20260812f.js`;
- `/static/app-20260812g.js` = HTTP 404;
- `/health` и `/ready` пока не содержат source build marker `83i`.

Вывод: приложение на Bothost живо, но контейнер на момент проверки всё ещё использует старую сборку `f`; полностью проверенный GitHub `main` ещё не задеплоен.

## Следующий обязательный live-шаг

В Bothost выполнить **Новый Deploy / пересборку из текущей ветки `main`**, не только Restart.

После deploy принять live только если одновременно выполняются условия:

- `/health` = 200 и `build=83i`;
- `/ready` = 200, `build=83i`, `database=true`;
- `/mini` = 200 без redirect;
- HTML использует `app-20260812g.js`;
- `/static/app-20260812g.js` = 200;
- неавторизованный `/api/accounts` = 403.

До выполнения этих условий нельзя считать, что пользователь в Telegram проверяет тот же код, который прошёл полный CI.

## Security хвост

Исторически опубликованные значения `MINIAPP_API_TOKEN` и `BACKUP_ENCRYPTION_KEY` считаются скомпрометированными. В текущем исходном дереве реальные значения удалены. Если они ещё не были заменены в панели Bothost, после стабильного deploy их необходимо ротировать там; приложение не имеет безопасного API записи env Bothost.
