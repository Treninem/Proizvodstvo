# Производственный бот — актуальная передача после Step84

## Исходная точка

Продолжать только с текущего `main` репозитория `Treninem/Proizvodstvo`. Не откатываться к Step82/Step83 runtime и не переписывать проект с нуля.

Проект: Telegram-бот + закрытый Telegram Mini App для производственного учёта.

Текущая проверенная source-версия:

- Bot `84`
- Backend `84a`
- Mini App `20260813a`
- tenant-isolation v2
- OWNER_TELEGRAM_ID=`2097006037`
- постоянная БД Bothost: `/app/data/production_account.sqlite3`
- порт: `3000`
- домен: `https://procontrol.bothost.tech`

Подробный итог: `STEP84_FINAL_AUDIT.md`.

## Что уже завершено и нельзя ломать

- мультитенантность: фирмы изолированы;
- создатель Telegram-группы получает административные права только своей организации;
- системный OWNER отделён от tenant-администрирования;
- отдельное OWNER-only `🔐 Системное меню`;
- точная версия видна только OWNER;
- мобильная навигация Mini App: `Производство · Склад · План · Отчёты · Ещё`;
- `Ещё` реально раскрывает дополнительные разделы, а не пустую страницу;
- физический склад: населённый пункт → площадка → участок → отдел → место хранения;
- двухсторонние передачи с резервированием, приёмкой, расхождениями и защитой от двойной приёмки;
- Excel import только preview → confirm с tenant-проверками и аудитом;
- Excel export;
- роли, сотрудники, отделы, оборудование, качество, ТО, задания, смены, заявки, партии, пополнение, отчёты, диагностика, backup/restore;
- недоступные функции скрываются в UI и блокируются сервером;
- production/stock/plan/reports/owner данные не должны течь между фирмами;
- активный frontend release использует cache-safe файл `app-20260813a.js`;
- `app.js` byte-identical активному JS;
- `style.css` byte-identical активному CSS;
- старые исполняемые JS/CSS runtime-файлы удалены;
- одноразовые Step84 patch/release generators удалены после выпуска релиза.

## Постоянные автоматические проверки

Главный workflow: `.github/workflows/full-system-audit-step83.yml`.

Последний полный source audit после очистки:

- run `31683328288`
- head `51102a7b907e4e5e6e060838801c818f86279b25`
- SUCCESS
- все 17 рабочих этапов зелёные.

Проверяется в том числе:

- Python compile;
- 798 SQL queries против актуальной SQLite-схемы;
- Ruff correctness;
- 123 API operations runtime smoke;
- active Mini App `node --check`;
- UI wiring: tabs/actions/DOM ids/primary mobile nav;
- tenant isolation и owner isolation;
- migration/backup/restore;
- transfers/warehouse/Excel/reports/diagnostics;
- unit + legacy QA;
- security/final/flow/smoke/runtime guard.

Постоянный UI wiring audit: `scripts/ui_wiring_audit_step84.py`. Он сам читает активный versioned JS из `index.html`, поэтому будущий релиз не требует вручную менять путь в audit-script.

## Реальный live deployment gate

Файлы:

- `scripts/live_deployment_gate_step84.py`
- `.github/workflows/live-deployment-gate-step84.yml`

Он проверяет реальный Bothost по build/version и SHA-256, а не только по HTTP 200.

## Текущий единственный внешний блокер

**GitHub source готов и полностью проверен, но Bothost не подхватил новый `main`.**

Live gate run `31683164892` сделал 30 попыток и во всех увидел старый runtime:

- `/mini` → `X-Mini-App-Version=20260812f`;
- `/health` и `/ready` без build `84a`;
- `/static/app-20260813a.js` → 404;
- live app.js/style.css/manifest отличаются от GitHub по SHA-256.

Следовательно пользователь сейчас через Telegram проверяет старый контейнер Bothost, а не source `84a / 20260813a`.

### Что делать при продолжении

1. **Не переделывать снова source-аудит и не откатывать код.**
2. В Bothost проверить, что production bot подключён именно к `Treninem/Proizvodstvo`, ветка `main`.
3. Выполнить полноценный **Deploy / пересборку**, не только Restart старого контейнера.
4. После deploy повторно запустить workflow `Live Step84 deployment gate`.
5. Принимать live только если одновременно:
   - `/health` = 200, `build=84a`;
   - `/ready` = 200, `database=true`, `build=84a`;
   - `/mini` = 200 без redirect;
   - `X-Mini-App-Version=20260813a`;
   - HTML использует `app-20260813a.js`;
   - `/static/app-20260813a.js` = 200 и SHA совпадает;
   - aliases/manifest совпадают;
   - unauth `/api/accounts` = 403.
6. Только после `LIVE_OK` продолжать пользовательское E2E в Telegram/Mini App на реальном аккаунте.

## Безопасность

- НЕ коммитить `.env`.
- НЕ коммитить `BOT_TOKEN`, `MINIAPP_API_TOKEN`, `BACKUP_ENCRYPTION_KEY` и другие реальные секреты.
- НЕ коммитить production SQLite, backup, logs и пользовательские выгрузки.
- Реальные секреты должны жить только в Bothost env.
- Исторически публиковавшиеся `MINIAPP_API_TOKEN` и `BACKUP_ENCRYPTION_KEY` считать скомпрометированными; если они ещё не ротированы в Bothost, заменить их после стабильного deploy.

## Правило продолжения

Не останавливаться на отчётах. Реальные дефекты исправлять, добавлять регрессионные проверки и коммитить в `Treninem/Proizvodstvo`. Но не объявлять live готовым, пока strict live gate фактически не показывает `LIVE_OK`.
