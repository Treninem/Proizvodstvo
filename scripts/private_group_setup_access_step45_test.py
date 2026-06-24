from __future__ import annotations

import asyncio
import os
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = Path('/tmp/prod_private_group_setup_step45')
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
os.environ['BOT_DATA_DIR'] = str(BASE)
os.environ['GLOBAL_OWNER_IDS'] = '2097006037'
os.environ['BOT_TOKEN'] = 'PUT_TELEGRAM_BOT_TOKEN_HERE'

aiogram_stub = types.ModuleType('aiogram')
aiogram_types_stub = types.ModuleType('aiogram.types')
class _Bot: pass
class _Chat:
    def __init__(self, chat_id: int, chat_type: str, title: str = '') -> None:
        self.id = chat_id
        self.type = chat_type
        self.title = title
        self.full_name = title
class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
aiogram_stub.Bot = _Bot
aiogram_types_stub.Chat = _Chat
aiogram_types_stub.User = _User
sys.modules.setdefault('aiogram', aiogram_stub)
sys.modules.setdefault('aiogram.types', aiogram_types_stub)

from app import db
from app.access import can_manage_accounting
from app.services import repository as repo

OWNER_1 = 111001
OWNER_2 = 111002
OTHER = 111003
PM_1 = 501001
PM_2 = 501002
PM_OTHER = 501003
GROUP_1 = -100111001
GROUP_2 = -100111002


async def main() -> None:
    db.init_db()
    account_1 = repo.ensure_group_account_context(GROUP_1, 'Группа 1', 'supergroup', OWNER_1, private_chat_id=PM_1, private_title='ЛС 1')
    assert account_1 is not None
    assert repo.get_active_account(GROUP_1).id == account_1.id
    assert repo.get_active_account(PM_1).id == account_1.id
    assert repo.resolve_scope_chat_id(PM_1) == account_1.scope_chat_id
    assert repo.resolve_scope_chat_id(GROUP_1) == account_1.scope_chat_id
    assert await can_manage_accounting(_Bot(), _Chat(PM_1, 'private', 'ЛС 1'), _User(OWNER_1))
    assert not await can_manage_accounting(_Bot(), _Chat(PM_OTHER, 'private', 'ЛС чужого'), _User(OTHER))

    ok, msg = repo.create_area(PM_1, 'Участок 1')
    assert ok, msg
    assert [a.name for a in repo.list_areas(GROUP_1)] == ['Участок 1']
    assert [a.name for a in repo.list_areas(PM_1)] == ['Участок 1']

    account_2 = repo.ensure_group_account_context(GROUP_2, 'Группа 2', 'supergroup', OWNER_2, private_chat_id=PM_2, private_title='ЛС 2')
    assert account_2 is not None
    assert account_2.id != account_1.id
    repo.create_entity(PM_2, 'component', 'Комплектующая 2')
    assert repo.get_entity_by_name(PM_2, 'component', 'Комплектующая 2')
    assert not repo.get_entity_by_name(PM_1, 'component', 'Комплектующая 2')

    private_account = repo.ensure_private_account_context(OTHER, PM_OTHER, 'ЛС обычного')
    assert private_account is not None
    assert await can_manage_accounting(_Bot(), _Chat(PM_OTHER, 'private', 'ЛС обычного'), _User(OTHER))


if __name__ == '__main__':
    asyncio.run(main())
    print('OK')
