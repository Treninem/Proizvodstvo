from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path('/tmp/prod_owner_foreign_account_step83')
shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True)
os.environ['BOT_DATA_DIR'] = str(BASE)
os.environ['OWNER_TELEGRAM_ID'] = '2097006037'
os.environ.setdefault('BOT_TOKEN', 'TEST_TOKEN_FOR_CI_ONLY')

from app import db
from app.services import accounting
from app.services import repository as repo


db.init_db()
PLATFORM_OWNER = 2097006037
TENANT_OWNER = 111222333
PLATFORM_PM = 9001
TENANT_PM = 9002
NORMAL_PM = 9003

repo.upsert_chat(PLATFORM_PM, 'ЛС владельца платформы', 'private', connected=True)
repo.upsert_chat(TENANT_PM, 'ЛС владельца фирмы', 'private', connected=True)
repo.upsert_chat(NORMAL_PM, 'ЛС обычного пользователя', 'private', connected=True)

ok, msg, account_id = repo.create_account(TENANT_OWNER, TENANT_PM, 'Чужая фирма')
assert ok and account_id, msg
account = repo.get_account_by_id(account_id)
assert account
scope = int(account.scope_chat_id)

# Системный владелец видит фирму только в отдельном системном контуре.
assert any(int(item.id) == int(account_id) for item in repo.owner_list_accounts())

# Но обычный tenant-доступ ему автоматически не выдаётся.
assert not repo.user_has_account_access(account_id, PLATFORM_OWNER)
ok, msg = repo.set_active_account(PLATFORM_PM, account_id, user_id=PLATFORM_OWNER)
assert not ok, (ok, msg)
assert repo.get_active_account(PLATFORM_PM) is None

# Посторонний пользователь также не может активировать чужой учёт.
ok, msg = repo.set_active_account(NORMAL_PM, account_id, user_id=555666777)
assert not ok, (ok, msg)

# Владелец конкретной фирмы сохраняет полный рабочий доступ к своему учёту.
ok, msg = repo.set_active_account(TENANT_PM, account_id, user_id=TENANT_OWNER)
assert ok, msg
assert repo.resolve_scope_chat_id(TENANT_PM) == scope

ok, msg = repo.create_entity(scope, 'component', 'Комплектующая 1')
assert ok, msg
entity = repo.get_entity_by_name(scope, 'component', 'Комплектующая 1')
assert entity
op = {
    'operation_type': 'production',
    'entity_type': 'component',
    'entity_id': entity.id,
    'entity_name': entity.name,
    'quantity': 5,
    'unit': 'шт',
    'needs_attention': False,
}
saved = accounting.apply_operations(scope, TENANT_PM, TENANT_OWNER, [op], 'Производство Комплектующая 1 5')
assert saved == 1
inv = db.fetchone('SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?', (scope, entity.id))
assert inv and float(inv['quantity']) == 5

print('owner_foreign_account_step83_test OK')
