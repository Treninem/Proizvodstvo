from __future__ import annotations

import os
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = Path('/tmp/prod_job_assignment_private_job_step46')
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
os.environ['BOT_DATA_DIR'] = str(BASE)
os.environ['GLOBAL_OWNER_IDS'] = '2097006037'
os.environ['BOT_TOKEN'] = 'PUT_TELEGRAM_BOT_TOKEN_HERE'

aiogram_stub = types.ModuleType('aiogram')
aiogram_types_stub = types.ModuleType('aiogram.types')
class _Bot: pass
class _Chat: pass
class _User: pass
aiogram_stub.Bot = _Bot
aiogram_types_stub.Chat = _Chat
aiogram_types_stub.User = _User
sys.modules.setdefault('aiogram', aiogram_stub)
sys.modules.setdefault('aiogram.types', aiogram_types_stub)

from app import db
from app.services import repository as repo

OWNER = 222001
GROUP = -100222001
TARGET = 222099


def main() -> None:
    db.init_db()
    private_account = repo.ensure_private_account_context(OWNER, OWNER, 'Личный чат')
    assert private_account is not None
    ok, msg = repo.create_job_title(OWNER, 'Пиу', {'production': True, 'reports': True})
    assert ok, msg
    assert repo.find_job_title(OWNER, 'Пиу')

    group_account = repo.ensure_group_account_context(GROUP, 'Рабочая группа', 'supergroup', OWNER)
    assert group_account is not None
    assert not repo.list_job_titles(GROUP)

    copied = repo.copy_job_titles_between_contexts(OWNER, GROUP)
    assert copied == 1, copied
    job = repo.find_job_title(GROUP, 'Пиу')
    assert job, 'Должность из личной настройки не перенеслась в учёт группы'
    repo.set_worker_job(GROUP, TARGET, 'Участник', int(job['id']))
    assert repo.visible_job_name(GROUP, TARGET) == 'Пиу'
    perms = repo.user_permissions_current_context(GROUP, TARGET)
    assert perms.get('production') is True
    assert perms.get('reports') is True


if __name__ == '__main__':
    main()
    print('OK')
