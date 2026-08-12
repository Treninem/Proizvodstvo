# Шаг 83 — security, tenant hardening, Excel reliability

Дата: 2026-08-12.
База: шаг 82 TENANT_UX_EXCEL_TRANSFERS.
Репозиторий: Treninem/Proizvodstvo.
Live: https://procontrol.bothost.tech
Mini App: 20260812a.

## Исправлено
- `delete_department_operation_rule`: `scope` определяется до `is_tenant_admin`.
- `delete_department_entity_rule`: то же исправление для удаления правила позиции отдела.
- Служебный `MINIAPP_API_TOKEN` больше не аутентифицирует произвольный `user_id`; сервисный ключ связан только с системным владельцем.
- Excel preview сохраняет все распознанные строки (до лимита распознавания), а не первые 1000.
- Excel confirm поддерживает безопасное возобновление после сбоя: batch `preview -> processing`, а строки получают детерминированный `client_request_id`; повторный запуск не создаёт дублей.
- ТО: `area_id` запчасти проверяется на принадлежность текущей организации.
- Пополнение: ручная заявка проверяет `area_id`; `source_rule_id` обязан принадлежать этой же организации и позиции/площадке.
- Качество: явно переданные `task_id`, `lot_id`, `equipment_id`, `shift_plan_id`, `parent_inspection_id`, `rework_task_id` обязаны существовать в текущей организации.
- Партии: `task_id` при `link_lots` обязан принадлежать текущей организации и быть доступен пользователю.
- Передачи, задания, партии, оборудование, качество, пополнение и ТО: отображающие JOIN усилены условием `chat_id`, чтобы повреждённые/старые межфирменные ссылки не раскрывали чужие названия.
- Разговорная фраза вида `Остаток трубы примерно 5 метров, надо проверить` больше не запускает инвентаризацию. Явные команды `Инвентаризация ...` и `Остаток ...` без разговорных оговорок продолжают работать.
- Зашифрованные резервные копии `.zip.enc` теперь отображаются в `list_backup_files()` наряду с обычными `.zip`.
- Старые QA-сценарии приведены в соответствие текущей модели: обязательные причины отмен/правок, encrypted backup, `WebAppInfo`, скрытое административное меню, tenant isolation и корректная семантика отгрузки.
- `runtime.defaults.env` не содержит реальных секретов; `.env`, SQLite, логи, бэкапы и экспорты исключены через `.gitignore`.

## Изоляция доступа
- Системный владелец платформы может перечислять фирмы только через отдельный системный контур.
- Автоматического обычного tenant-доступа к чужой фирме у системного владельца нет.
- Владелец конкретной фирмы сохраняет полный рабочий доступ только к своему учёту.
- Посторонний пользователь не может активировать чужой tenant.

## Проверено локально и в GitHub Actions
- `python -m compileall -q app webapp`: OK.
- `node --check webapp/static/app-20260812a.js`: OK.
- Step83 regression suite: 11/11 OK.
- Runtime config check с тестовыми env: OK.
- Живой SQLite smoke-test ТО после tenant-JOIN hardening: OK.
- Миграция реальной сохранённой БД step81 -> step83: старые аккаунт/позиция/площадка/операция и остаток 17 шт. сохранены; `PRAGMA integrity_check=ok`.
- Backup/restore новых таблиц step82/83: площадки, места хранения, передачи, строки передачи, Excel batch и tenant audit восстановлены; integrity `ok`.
- Secret-scan рабочей сборки: реальных токенов/ключей не найдено; только безопасные placeholders.
- Полный GitHub Actions QA run `31625440054`, job `94210531222`: SUCCESS.
- В полном прогоне успешно прошли compile, JS, все 11 Step83 regressions и весь legacy QA-набор шагов 37–57/отчётов/групп/доступов/security/UI/final/smoke.
- Cleanup commit после полного QA: `004427b4a811a9d0f6bc55ff2e046503977a486b` (`CI: complete full Step 83 QA`).

## GitHub
- Security commit `8def5e18a1b6005ec2e950e8d2008f49bef458f1`: удалены опубликованные секреты из `runtime.defaults.env`.
- Commit `fd2527866c37148e5a9da8c06c335f3782e19bda`: `.gitignore` для секретов/runtime-данных.
- Commit `8f992e521cbd83995f82d7a34d2cd9a08f2c7fe8`: безопасный `.env.example`.
- Step83 complete source commit `68388afec4f1df720883039f424415fb58ab341e`: полный tenant-safe production update.
- Inventory conversational guard commit `63368a7ef4793ca621574c4fb726963a4158ba62`.
- Encrypted backup listing fix был проверен отдельным CI, после чего Step83 regressions стали 11/11.
- Полный QA завершён и одноразовые workflow удалены после успеха.

## Bothost live — подтверждено
Final live probe: run `31625597428`, job `94211056429`: SUCCESS.
- `/health` -> HTTP 200; bot_enabled=true, miniapp_enabled=true.
- `/ready` -> HTTP 200; database=true, database_latency_ms=0.3, mini_app=true.
- `/mini` -> HTTP 200.
- Live HTML реально использует `/static/app-20260812a.js?v=82-ux` и `/static/style-20260812a.css?v=82-ux`.
- `Last-Modified` live Mini App: Wed, 12 Aug 2026 17:39:10 GMT.
- Одноразовый live probe удалён cleanup-коммитом `559b68c084d27fc397a5473be881086a5f3e8a00`.

## Единственный оставшийся security-шаг
Ранее опубликованные в публичной истории GitHub значения `MINIAPP_API_TOKEN` и `BACKUP_ENCRYPTION_KEY` считаются скомпрометированными. В текущем `main` они удалены и пусты в `runtime.defaults.env`, но их необходимо ротировать в переменных окружения Bothost.

Приложение не содержит Bothost Agent/API или другого механизма записи переменных окружения: `app/config.py` только читает env при запуске, а README указывает задавать переменные в панели Bothost. Прямого Bothost-коннектора в этой сессии нет, поэтому ротация этих двух env — единственное действие, которое нельзя безопасно выполнить из текущих инструментов.
