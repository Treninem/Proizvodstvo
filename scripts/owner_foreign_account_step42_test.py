import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path('/tmp/prod_owner_foreign_account_step42')
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
os.environ['BOT_DATA_DIR'] = str(BASE)
os.environ['GLOBAL_OWNER_IDS'] = '2097006037'
os.environ['BOT_TOKEN'] = 'PUT_TELEGRAM_BOT_TOKEN_HERE'

from app import db
from app.services import accounting
from app.services import repository as repo

import types
aiogram_stub = types.ModuleType("aiogram")
aiogram_types_stub = types.ModuleType("aiogram.types")
class _Router:
    pass
class _Bot:
    pass
class _Message:
    pass
class _Chat:
    pass
class _User:
    pass
aiogram_stub.Router = _Router
aiogram_stub.Bot = _Bot
aiogram_types_stub.Message = _Message
aiogram_types_stub.Chat = _Chat
aiogram_types_stub.User = _User
sys.modules.setdefault("aiogram", aiogram_stub)
sys.modules.setdefault("aiogram.types", aiogram_types_stub)
from app.handlers.accounts import _account_choices_text, _find_account_for_owner

db.init_db()
GLOBAL_OWNER = 2097006037
OTHER_OWNER_1 = 111222333
OTHER_OWNER_2 = 111222334
OWNER_PM = 9001
OTHER_PM_1 = 9002
OTHER_PM_2 = 9003
NORMAL_USER_PM = 9004

repo.upsert_chat(OWNER_PM, 'ЛС владельца бота', 'private', connected=True)
repo.upsert_chat(OTHER_PM_1, 'ЛС владельца учёта 1', 'private', connected=True)
repo.upsert_chat(OTHER_PM_2, 'ЛС владельца учёта 2', 'private', connected=True)
repo.upsert_chat(NORMAL_USER_PM, 'ЛС обычного пользователя', 'private', connected=True)

ok1, msg1, account_id_1 = repo.create_account(OTHER_OWNER_1, OTHER_PM_1, 'Смена')
ok2, msg2, account_id_2 = repo.create_account(OTHER_OWNER_2, OTHER_PM_2, 'Смена')
assert ok1 and account_id_1, msg1
assert ok2 and account_id_2, msg2
accounts = repo.owner_list_accounts()
assert _account_choices_text(accounts, 'Смена') is not None
assert _find_account_for_owner(accounts, f'№{account_id_1}').id == account_id_1

account = repo.get_account_by_id(account_id_1)
scope = account.scope_chat_id
repo.create_entity(scope, 'component', 'Комплектующая 1')
entity = repo.get_entity_by_name(scope, 'component', 'Комплектующая 1')
assert entity

ok, msg = repo.set_active_account(OWNER_PM, account_id_1, user_id=GLOBAL_OWNER)
assert ok, msg
assert repo.resolve_scope_chat_id(OWNER_PM) == scope

ok, msg = repo.set_active_account(NORMAL_USER_PM, account_id_1, user_id=555666777)
assert not ok

op = {
    'operation_type': 'production',
    'entity_type': 'component',
    'entity_id': entity.id,
    'entity_name': entity.name,
    'quantity': 5000,
    'unit': 'шт',
    'needs_attention': False,
}
saved = accounting.apply_operations(scope, OWNER_PM, GLOBAL_OWNER, [op], 'Производство Комплектующая 1 5000')
assert saved == 1
operation_id = int(db.fetchone('SELECT id FROM operations WHERE chat_id=?', (scope,))['id'])
inv = db.fetchone('SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?', (scope, entity.id))
assert inv and float(inv['quantity']) == 5000

ok, msg = accounting.change_operation_quantity(scope, OWNER_PM, GLOBAL_OWNER, operation_id, 7000)
assert ok, msg
inv = db.fetchone('SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?', (scope, entity.id))
assert inv and float(inv['quantity']) == 7000

repo.set_user_test_mode(GLOBAL_OWNER, True)
saved = accounting.apply_operations(scope, OWNER_PM, GLOBAL_OWNER, [op | {'quantity': 999999}], '#тест Производство Комплектующая 1 999999', dry_run=True)
assert saved == 1
inv = db.fetchone('SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?', (scope, entity.id))
assert inv and float(inv['quantity']) == 7000

print('OK')
