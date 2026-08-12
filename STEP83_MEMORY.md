# Шаг 83 — security, tenant hardening, Excel reliability

Дата: 2026-08-12.
База: шаг 82 TENANT_UX_EXCEL_TRANSFERS.
Репозиторий: Treninem/Proizvodstvo.

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
- `runtime.defaults.env` не содержит реальных секретов; `.env`, SQLite, логи, бэкапы и экспорты исключены через `.gitignore`.

## Проверено
- `python -m compileall -q app webapp scripts main.py`: OK.
- `node --check webapp/static/app-20260812a.js`: OK.
- `python -m unittest -v tests.test_step83_regressions`: 9/9 OK.
- Runtime config check с тестовыми env: OK.
- Живой SQLite smoke-test ТО после tenant-JOIN hardening: OK.
- Миграция реальной сохранённой БД step81 -> step83: старые аккаунт/позиция/площадка/операция и остаток 17 шт. сохранены; `PRAGMA integrity_check=ok`.
- Backup/restore новых таблиц step82/83: площадки, места хранения, передачи, строки передачи, Excel batch и tenant audit восстановлены; integrity `ok`.
- Secret-scan рабочей сборки: реальных токенов/ключей не найдено; только безопасные placeholders в `.env.example`.

## GitHub уже выполнено
- Security commit `8def5e18a1b6005ec2e950e8d2008f49bef458f1`: удалены опубликованные секреты из `runtime.defaults.env`.
- Commit `fd2527866c37148e5a9da8c06c335f3782e19bda`: добавлен `.gitignore` для секретов/runtime-данных.
- Commit `8f992e521cbd83995f82d7a34d2cd9a08f2c7fe8`: обновлён безопасный `.env.example`.
- Commit `f2df4f16581faf65a641da1510f35ab2a6cc5641`: добавлены регрессионные тесты шага 83.

## Внешний блокер
Секреты, ранее опубликованные в публичном GitHub, остаются скомпрометированными историей Git и должны быть заменены новыми значениями в Bothost. Прямого Bothost-коннектора в текущей сессии нет.
GitHub-коннектор умеет записи файлов/tree/commit, но не принимает локальную папку как bulk-upload; полный перенос исходников step82->83 выполняется по файлам.
