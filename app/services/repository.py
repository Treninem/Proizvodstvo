from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .. import db
from ..config import settings
from .normalize import normalize_key, split_aliases


@dataclass(frozen=True)
class Entity:
    id: int
    chat_id: int
    entity_type: str
    name: str
    normalized: str
    default_unit: str


@dataclass(frozen=True)
class Area:
    id: int
    chat_id: int
    name: str
    normalized: str


@dataclass(frozen=True)
class AccountingAccount:
    id: int
    owner_user_id: int
    owner_chat_id: int
    scope_chat_id: int
    name: str
    normalized: str
    is_general: bool


def _account_from_row(r) -> AccountingAccount:
    return AccountingAccount(
        id=int(r["id"]),
        owner_user_id=int(r["owner_user_id"]),
        owner_chat_id=int(r["owner_chat_id"]),
        scope_chat_id=int(r["scope_chat_id"]),
        name=str(r["name"]),
        normalized=str(r["normalized"]),
        is_general=bool(r["is_general"]),
    )


PERMISSION_KEYS = {
    "production", "material", "energy", "assembly", "shipment",
    "reports", "stock", "edit", "setup", "workers", "grant",
    "permissions", "export",
}


def full_permissions() -> dict[str, bool]:
    return {key: True for key in PERMISSION_KEYS}


def _permissions_from_job_id(job_id: int | None) -> dict[str, bool]:
    if not job_id:
        return {}
    row = db.fetchone("SELECT permissions_json FROM job_titles WHERE id=? AND is_archived=0", (job_id,))
    if not row:
        return {}
    try:
        return json.loads(row["permissions_json"] or "{}")
    except Exception:
        return {}


def _access_flags_for_job(job_id: int | None) -> tuple[int, int, int]:
    permissions = _permissions_from_job_id(job_id)
    can_manage = bool(permissions.get("setup") or permissions.get("workers") or permissions.get("grant") or permissions.get("permissions"))
    can_submit = bool(any(permissions.get(key) for key in ("production", "material", "energy", "assembly", "shipment", "stock")))
    can_view = bool(can_manage or can_submit or permissions.get("reports") or permissions.get("stock") or permissions.get("export"))
    return (1 if can_manage else 0, 1 if can_view else 0, 1 if can_submit else 0)


def is_global_owner_id(user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.global_owner_ids)


def get_active_account(chat_id: int) -> AccountingAccount | None:
    row = db.fetchone(
        """
        SELECT a.* FROM chat_active_account ca
        JOIN accounting_accounts a ON a.id=ca.account_id
        WHERE ca.chat_id=? AND a.is_archived=0
        """,
        (chat_id,),
    )
    return _account_from_row(row) if row else None


def resolve_scope_chat_id(chat_id: int) -> int:
    account = get_active_account(chat_id)
    return account.scope_chat_id if account else chat_id


def create_account(owner_user_id: int, owner_chat_id: int, name: str, is_general: bool = False) -> tuple[bool, str, int | None]:
    key = normalize_key(name)
    if not key:
        return False, "Название учёта не найдено.", None
    try:
        with db.connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            cur = conn.execute(
                """
                INSERT INTO accounting_accounts(owner_user_id,owner_chat_id,scope_chat_id,name,normalized,is_general)
                VALUES(?,?,?,?,?,?)
                """,
                (owner_user_id, owner_chat_id, 0, name.strip(), key, 1 if is_general else 0),
            )
            account_id = int(cur.lastrowid)
            scope_chat_id = -900000000000 - account_id
            conn.execute("UPDATE accounting_accounts SET scope_chat_id=? WHERE id=?", (scope_chat_id, account_id))
            conn.execute(
                "INSERT OR IGNORE INTO chats(chat_id,title,chat_type,is_connected) VALUES(?,?,?,1)",
                (scope_chat_id, f"Учёт: {name.strip()}", "account"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO account_chat_access(account_id,chat_id,can_manage,can_view,can_submit)
                VALUES(?,?,?,?,?)
                """,
                (account_id, owner_chat_id, 1, 1, 1),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO account_user_access(account_id,user_id,job_title_id,can_manage,can_view,can_submit,updated_at)
                VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                """,
                (account_id, owner_user_id, None, 1, 1, 1),
            )
            conn.execute(
                "INSERT OR REPLACE INTO chat_active_account(chat_id,account_id,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                (owner_chat_id, account_id),
            )
            conn.commit()
        return True, f"Учёт создан: {name.strip()}", account_id
    except Exception:
        return False, "Такой учёт уже есть или его не удалось создать.", None


def get_account_by_id(account_id: int) -> AccountingAccount | None:
    row = db.fetchone("SELECT * FROM accounting_accounts WHERE id=? AND is_archived=0", (account_id,))
    return _account_from_row(row) if row else None


def get_account_by_scope(scope_chat_id: int) -> AccountingAccount | None:
    row = db.fetchone("SELECT * FROM accounting_accounts WHERE scope_chat_id=? AND is_archived=0", (scope_chat_id,))
    return _account_from_row(row) if row else None


def list_accounts_for_chat(chat_id: int) -> list[AccountingAccount]:
    rows = db.fetchall(
        """
        SELECT a.* FROM accounting_accounts a
        JOIN account_chat_access ac ON ac.account_id=a.id
        WHERE ac.chat_id=? AND a.is_archived=0
        ORDER BY a.is_general DESC, a.name
        """,
        (chat_id,),
    )
    return [_account_from_row(r) for r in rows]


def list_accounts_for_user(user_id: int, chat_id: int | None = None, include_accessible: bool = True) -> list[AccountingAccount]:
    rows = db.fetchall(
        "SELECT * FROM accounting_accounts WHERE owner_user_id=? AND is_archived=0 ORDER BY is_general DESC, name",
        (user_id,),
    )
    accounts = [_account_from_row(r) for r in rows]
    seen = {a.id for a in accounts}
    if include_accessible:
        user_rows = db.fetchall(
            """
            SELECT a.* FROM accounting_accounts a
            JOIN account_user_access ua ON ua.account_id=a.id
            WHERE ua.user_id=? AND a.is_archived=0
            ORDER BY a.is_general DESC, a.name
            """,
            (user_id,),
        )
        for r in user_rows:
            acc = _account_from_row(r)
            if acc.id not in seen:
                accounts.append(acc)
                seen.add(acc.id)
        if chat_id is not None:
            for acc in list_accounts_for_chat(chat_id):
                if acc.id not in seen:
                    accounts.append(acc)
                    seen.add(acc.id)
    return accounts


def find_account_for_chat(chat_id: int, name: str) -> AccountingAccount | None:
    key = normalize_key(name)
    rows = db.fetchall(
        """
        SELECT a.* FROM accounting_accounts a
        JOIN account_chat_access ac ON ac.account_id=a.id
        WHERE ac.chat_id=? AND a.normalized=? AND a.is_archived=0
        """,
        (chat_id, key),
    )
    return _account_from_row(rows[0]) if rows else None


def chat_has_account_access(chat_id: int, account_id: int, require_manage: bool = False) -> bool:
    row = db.fetchone(
        "SELECT can_manage,can_view,can_submit FROM account_chat_access WHERE chat_id=? AND account_id=?",
        (chat_id, account_id),
    )
    if not row:
        return False
    if require_manage:
        return bool(row["can_manage"])
    return bool(row["can_view"] or row["can_submit"] or row["can_manage"])


def attach_chat_to_account(account_id: int, chat_id: int, can_manage: bool = False, set_active: bool = True) -> None:
    db.execute(
        """
        INSERT OR REPLACE INTO account_chat_access(account_id,chat_id,can_manage,can_view,can_submit)
        VALUES(?,?,?,?,?)
        """,
        (account_id, chat_id, 1 if can_manage else 0, 1, 1),
    )
    if set_active:
        db.execute(
            "INSERT OR REPLACE INTO chat_active_account(chat_id,account_id,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)",
            (chat_id, account_id),
        )


def user_has_account_access(account_id: int, user_id: int | None, require_manage: bool = False) -> bool:
    if is_global_owner_id(user_id):
        return True
    if not user_id:
        return False
    account = get_account_by_id(account_id)
    if account and account.owner_user_id == user_id:
        return True
    row = db.fetchone(
        "SELECT can_manage,can_view,can_submit FROM account_user_access WHERE account_id=? AND user_id=?",
        (account_id, user_id),
    )
    if not row:
        return False
    if require_manage:
        return bool(row["can_manage"])
    return bool(row["can_manage"] or row["can_view"] or row["can_submit"])


def grant_account_user_access(account_id: int, user_id: int, job_title_id: int | None, display_manage: bool | None = None) -> None:
    can_manage, can_view, can_submit = _access_flags_for_job(job_title_id)
    if display_manage is not None:
        can_manage = 1 if display_manage else can_manage
        can_view = 1 if display_manage else can_view
        can_submit = 1 if display_manage else can_submit
    db.execute(
        """
        INSERT INTO account_user_access(account_id,user_id,job_title_id,can_manage,can_view,can_submit,updated_at)
        VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(account_id,user_id) DO UPDATE SET
            job_title_id=excluded.job_title_id,
            can_manage=excluded.can_manage,
            can_view=excluded.can_view,
            can_submit=excluded.can_submit,
            updated_at=CURRENT_TIMESTAMP
        """,
        (account_id, user_id, job_title_id, can_manage, can_view, can_submit),
    )


def user_can_manage_current_context(chat_id: int, user_id: int | None) -> bool:
    if is_global_owner_id(user_id):
        return True
    account = get_active_account(chat_id)
    if account:
        return user_has_account_access(account.id, user_id, require_manage=True)
    permissions = worker_permissions(chat_id, user_id or 0)
    return bool(permissions.get("setup") or permissions.get("workers") or permissions.get("grant") or permissions.get("permissions"))


def user_permissions_current_context(chat_id: int, user_id: int | None) -> dict[str, bool]:
    if is_global_owner_id(user_id):
        return full_permissions()
    account = get_active_account(chat_id)
    if account and user_id:
        if account.owner_user_id == user_id:
            return full_permissions()
        row = db.fetchone(
            "SELECT job_title_id,can_manage,can_view,can_submit FROM account_user_access WHERE account_id=? AND user_id=?",
            (account.id, user_id),
        )
        if row:
            return _permissions_from_job_id(int(row["job_title_id"]) if row["job_title_id"] else None)
    return worker_permissions(chat_id, user_id or 0)


def visible_job_name(chat_id: int, user_id: int) -> str | None:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT j.name FROM workers w
        LEFT JOIN job_titles j ON j.id=w.job_title_id
        WHERE w.chat_id=? AND w.user_id=? AND w.is_active=1
        """,
        (scope, user_id),
    )
    return str(row["name"]) if row and row["name"] else None


def set_active_account(chat_id: int, account_id: int, user_id: int | None = None) -> tuple[bool, str]:
    allowed_by_chat = chat_has_account_access(chat_id, account_id)
    allowed_by_user = user_has_account_access(account_id, user_id)
    if not (allowed_by_chat or allowed_by_user):
        return False, "Этот учёт не подключён к текущему чату."
    db.execute(
        "INSERT OR REPLACE INTO chat_active_account(chat_id,account_id,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)",
        (chat_id, account_id),
    )
    account = db.fetchone("SELECT name FROM accounting_accounts WHERE id=?", (account_id,))
    return True, f"Активный учёт: {account['name'] if account else account_id}"


def list_account_chats(account_id: int) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT c.chat_id,c.title,c.chat_type,c.is_connected,ac.can_manage,ac.can_view,ac.can_submit
        FROM account_chat_access ac
        JOIN chats c ON c.chat_id=ac.chat_id
        WHERE ac.account_id=?
        ORDER BY c.title
        """,
        (account_id,),
    )
    return [dict(r) for r in rows]


def account_summary_for_chat(chat_id: int, user_id: int | None = None) -> str:
    active = get_active_account(chat_id)
    accounts = list_accounts_for_user(user_id or 0, chat_id) if user_id else list_accounts_for_chat(chat_id)
    lines = ["Учёты"]
    if active:
        lines.append(f"\nАктивный учёт: {active.name}")
    else:
        lines.append("\nАктивный учёт не выбран. Сейчас данные идут в учёт текущей группы.")
    if accounts:
        lines.append("\nДоступные учёты:")
        for acc in accounts:
            mark = "✅" if active and active.id == acc.id else "▫️"
            common = " · общий" if acc.is_general else ""
            lines.append(f"{mark} {acc.name}{common}")
    else:
        lines.append("\nДоступных учётов пока нет.")
    job = visible_job_name(chat_id, user_id) if user_id else None
    if job:
        lines.append(f"\nВаша должность здесь: {job}")
    lines.append("\nКоманды: создать учёт Название, выбрать учёт Название, подключить чат к учёту Название.")
    return "\n".join(lines)


def upsert_chat(chat_id: int, title: str = "", chat_type: str = "", connected: bool | None = None) -> None:
    existing = db.fetchone("SELECT chat_id FROM chats WHERE chat_id=?", (chat_id,))
    is_connected = 1 if connected else 0
    if existing:
        if connected is None:
            db.execute("UPDATE chats SET title=?, chat_type=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (title, chat_type, chat_id))
        else:
            db.execute("UPDATE chats SET title=?, chat_type=?, is_connected=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (title, chat_type, is_connected, chat_id))
    else:
        db.execute("INSERT INTO chats(chat_id,title,chat_type,is_connected) VALUES(?,?,?,?)", (chat_id, title, chat_type, is_connected))


def is_connected_chat(chat_id: int) -> bool:
    row = db.fetchone("SELECT is_connected FROM chats WHERE chat_id=?", (chat_id,))
    return bool(row and row["is_connected"])


def set_chat_connected(chat_id: int, title: str, chat_type: str, connected: bool = True) -> None:
    upsert_chat(chat_id, title, chat_type, connected)


def create_area(chat_id: int, name: str) -> tuple[bool, str]:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    try:
        db.execute("INSERT INTO areas(chat_id,name,normalized) VALUES(?,?,?)", (chat_id, name.strip(), key))
        return True, f"Участок создан: {name.strip()}"
    except Exception:
        return False, "Такой участок уже есть."


def list_areas(chat_id: int) -> list[Area]:
    chat_id = resolve_scope_chat_id(chat_id)
    rows = db.fetchall("SELECT * FROM areas WHERE chat_id=? AND is_archived=0 ORDER BY name", (chat_id,))
    return [Area(int(r["id"]), int(r["chat_id"]), r["name"], r["normalized"]) for r in rows]


def bind_chat_to_area(group_chat_id: int, area_id: int | None) -> None:
    db.execute("INSERT OR REPLACE INTO chat_area_bindings(chat_id, area_id) VALUES(?,?)", (group_chat_id, area_id))


def get_bound_area(group_chat_id: int) -> Area | None:
    row = db.fetchone("""
        SELECT a.* FROM chat_area_bindings b
        JOIN areas a ON a.id=b.area_id
        WHERE b.chat_id=? AND a.is_archived=0
    """, (group_chat_id,))
    if not row:
        return None
    return Area(int(row["id"]), int(row["chat_id"]), row["name"], row["normalized"])


def create_entity(chat_id: int, entity_type: str, name: str, default_unit: str = "шт") -> tuple[bool, str]:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    try:
        db.execute(
            "INSERT INTO entities(chat_id,entity_type,name,normalized,default_unit) VALUES(?,?,?,?,?)",
            (chat_id, entity_type, name.strip(), key, default_unit),
        )
        return True, f"Создано: {name.strip()}"
    except Exception:
        return False, "Такая позиция уже есть."


def list_entities(chat_id: int, entity_types: Iterable[str] | None = None) -> list[Entity]:
    chat_id = resolve_scope_chat_id(chat_id)
    if entity_types:
        types = list(entity_types)
        marks = ",".join("?" for _ in types)
        rows = db.fetchall(
            f"SELECT * FROM entities WHERE chat_id=? AND entity_type IN ({marks}) AND is_archived=0 ORDER BY name",
            (chat_id, *types),
        )
    else:
        rows = db.fetchall("SELECT * FROM entities WHERE chat_id=? AND is_archived=0 ORDER BY name", (chat_id,))
    return [Entity(int(r["id"]), int(r["chat_id"]), r["entity_type"], r["name"], r["normalized"], r["default_unit"]) for r in rows]


def get_entity(entity_id: int) -> Entity | None:
    r = db.fetchone("SELECT * FROM entities WHERE id=? AND is_archived=0", (entity_id,))
    if not r:
        return None
    return Entity(int(r["id"]), int(r["chat_id"]), r["entity_type"], r["name"], r["normalized"], r["default_unit"])


def add_aliases(chat_id: int, target_type: str, target_id: int, aliases_text: str, source: str = "manual") -> tuple[int, list[str]]:
    chat_id = resolve_scope_chat_id(chat_id)
    added = 0
    conflicts: list[str] = []
    for alias in split_aliases(aliases_text):
        key = normalize_key(alias)
        existing = db.fetchone("SELECT target_type,target_id FROM aliases WHERE chat_id=? AND normalized=?", (chat_id, key))
        if existing and (existing["target_type"] != target_type or int(existing["target_id"]) != target_id):
            conflicts.append(alias)
            continue
        db.execute(
            "INSERT OR IGNORE INTO aliases(chat_id,target_type,target_id,alias,normalized,source) VALUES(?,?,?,?,?,?)",
            (chat_id, target_type, target_id, alias.strip(), key, source),
        )
        added += 1
    return added, conflicts


def list_alias_candidates(chat_id: int) -> list[dict]:
    chat_id = resolve_scope_chat_id(chat_id)
    result: list[dict] = []
    for area in list_areas(chat_id):
        result.append({"target_type": "area", "target_id": area.id, "name": area.name, "key": area.normalized, "source": "area"})
    for ent in list_entities(chat_id):
        result.append({"target_type": ent.entity_type, "target_id": ent.id, "name": ent.name, "key": ent.normalized, "source": "entity"})
    rows = db.fetchall("SELECT * FROM aliases WHERE chat_id=?", (chat_id,))
    by_entity = {e.id: e for e in list_entities(chat_id)}
    by_area = {a.id: a for a in list_areas(chat_id)}
    for r in rows:
        t = r["target_type"]
        tid = int(r["target_id"])
        name = ""
        if t == "area" and tid in by_area:
            name = by_area[tid].name
        elif tid in by_entity:
            name = by_entity[tid].name
        if name:
            result.append({"target_type": t, "target_id": tid, "name": name, "key": r["normalized"], "source": "alias"})
    lex = db.fetchall("SELECT * FROM local_lexicon WHERE chat_id=?", (chat_id,))
    for r in lex:
        t = r["target_type"]
        tid = int(r["target_id"])
        name = ""
        if t == "area" and tid in by_area:
            name = by_area[tid].name
        elif tid in by_entity:
            name = by_entity[tid].name
        if name:
            result.append({"target_type": t, "target_id": tid, "name": name, "key": r["normalized"], "source": "lexicon"})
    return result


def remember_lexicon(chat_id: int, phrase: str, target_type: str, target_id: int) -> None:
    key = normalize_key(phrase)
    if not key:
        return
    existing = db.fetchone(
        "SELECT target_type,target_id FROM local_lexicon WHERE chat_id=? AND normalized=?",
        (chat_id, key),
    )
    if existing and (existing["target_type"] != target_type or int(existing["target_id"]) != int(target_id)):
        return
    alias_conflict = db.fetchone(
        "SELECT target_type,target_id FROM aliases WHERE chat_id=? AND normalized=?",
        (chat_id, key),
    )
    if alias_conflict and (alias_conflict["target_type"] != target_type or int(alias_conflict["target_id"]) != int(target_id)):
        return
    db.execute(
        "INSERT OR REPLACE INTO local_lexicon(chat_id,phrase,normalized,target_type,target_id) VALUES(?,?,?,?,?)",
        (chat_id, phrase.strip(), key, target_type, int(target_id)),
    )


def create_job_title(chat_id: int, name: str, permissions: dict[str, bool] | None = None) -> tuple[bool, str]:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    try:
        db.execute(
            "INSERT INTO job_titles(chat_id,name,normalized,permissions_json) VALUES(?,?,?,?)",
            (chat_id, name.strip(), key, json.dumps(permissions or {}, ensure_ascii=False)),
        )
        return True, f"Должность создана: {name.strip()}"
    except Exception:
        return False, "Такая должность уже есть."


def list_job_titles(chat_id: int) -> list[dict]:
    chat_id = resolve_scope_chat_id(chat_id)
    rows = db.fetchall("SELECT * FROM job_titles WHERE chat_id=? AND is_archived=0 ORDER BY name", (chat_id,))
    return [dict(r) for r in rows]


def update_job_permissions(chat_id: int, job_id: int, permissions: dict[str, bool]) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute(
        "UPDATE job_titles SET permissions_json=? WHERE chat_id=? AND id=?",
        (json.dumps(permissions, ensure_ascii=False), chat_id, job_id),
    )


def find_job_title(chat_id: int, name: str) -> dict | None:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    row = db.fetchone("SELECT * FROM job_titles WHERE chat_id=? AND normalized=? AND is_archived=0", (chat_id, key))
    return dict(row) if row else None


def set_setup_session(chat_id: int, user_id: int, state: str, data: dict | None = None) -> None:
    db.execute(
        """
        INSERT OR REPLACE INTO setup_sessions(chat_id,user_id,state,data_json,updated_at)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (chat_id, user_id, state, json.dumps(data or {}, ensure_ascii=False)),
    )


def get_setup_session(chat_id: int, user_id: int) -> dict | None:
    row = db.fetchone("SELECT * FROM setup_sessions WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    if not row:
        return None
    return {"state": row["state"], "data": json.loads(row["data_json"] or "{}")}


def clear_setup_session(chat_id: int, user_id: int) -> None:
    db.execute("DELETE FROM setup_sessions WHERE chat_id=? AND user_id=?", (chat_id, user_id))


def count_active_areas(chat_id: int) -> int:
    chat_id = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT COUNT(*) AS n FROM areas WHERE chat_id=? AND is_archived=0", (chat_id,))
    return int(row["n"] if row else 0)


def archive_area(chat_id: int, area_id: int) -> bool:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute("UPDATE areas SET is_archived=1 WHERE chat_id=? AND id=?", (chat_id, area_id))
    return True


def archive_entity(chat_id: int, entity_id: int) -> bool:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute("UPDATE entities SET is_archived=1 WHERE chat_id=? AND id=?", (chat_id, entity_id))
    return True


def bind_meter_to_areas(chat_id: int, meter_id: int, area_ids: list[int]) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute("DELETE FROM meter_area_bindings WHERE meter_id=?", (meter_id,))
    for area_id in area_ids:
        area = db.fetchone("SELECT id FROM areas WHERE chat_id=? AND id=? AND is_archived=0", (chat_id, area_id))
        if area:
            db.execute("INSERT OR IGNORE INTO meter_area_bindings(meter_id,area_id) VALUES(?,?)", (meter_id, area_id))


def list_meter_area_ids(meter_id: int) -> list[int]:
    rows = db.fetchall("SELECT area_id FROM meter_area_bindings WHERE meter_id=? ORDER BY area_id", (meter_id,))
    return [int(r["area_id"]) for r in rows]


def list_meter_area_names(meter_id: int) -> list[str]:
    rows = db.fetchall(
        """
        SELECT a.name FROM meter_area_bindings b
        JOIN areas a ON a.id=b.area_id
        WHERE b.meter_id=? AND a.is_archived=0
        ORDER BY a.name
        """,
        (meter_id,),
    )
    return [str(r["name"]) for r in rows]


def get_entity_by_name(chat_id: int, entity_type: str, name: str) -> Entity | None:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    row = db.fetchone(
        "SELECT * FROM entities WHERE chat_id=? AND entity_type=? AND normalized=? AND is_archived=0",
        (chat_id, entity_type, key),
    )
    if not row:
        return None
    return Entity(int(row["id"]), int(row["chat_id"]), row["entity_type"], row["name"], row["normalized"], row["default_unit"])


def get_area(area_id: int) -> Area | None:
    row = db.fetchone("SELECT * FROM areas WHERE id=? AND is_archived=0", (area_id,))
    if not row:
        return None
    return Area(int(row["id"]), int(row["chat_id"]), row["name"], row["normalized"])


def bind_stock_item_to_areas(chat_id: int, stock_item_id: int, area_ids: list[int]) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute("DELETE FROM stock_item_area_bindings WHERE stock_item_id=?", (stock_item_id,))
    for area_id in area_ids:
        area = db.fetchone("SELECT id FROM areas WHERE chat_id=? AND id=? AND is_archived=0", (chat_id, area_id))
        item = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='stock_item' AND is_archived=0", (chat_id, stock_item_id))
        if area and item:
            db.execute("INSERT OR IGNORE INTO stock_item_area_bindings(stock_item_id,area_id) VALUES(?,?)", (stock_item_id, area_id))


def list_stock_item_area_ids(stock_item_id: int) -> list[int]:
    rows = db.fetchall("SELECT area_id FROM stock_item_area_bindings WHERE stock_item_id=? ORDER BY area_id", (stock_item_id,))
    return [int(r["area_id"]) for r in rows]


def list_stock_item_area_names(stock_item_id: int) -> list[str]:
    rows = db.fetchall(
        """
        SELECT a.name FROM stock_item_area_bindings b
        JOIN areas a ON a.id=b.area_id
        WHERE b.stock_item_id=? AND a.is_archived=0
        ORDER BY a.name
        """,
        (stock_item_id,),
    )
    return [str(r["name"]) for r in rows]


def list_meters_for_area(chat_id: int, area_id: int) -> list[Entity]:
    chat_id = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT e.* FROM meter_area_bindings b
        JOIN entities e ON e.id=b.meter_id
        WHERE e.chat_id=? AND b.area_id=? AND e.entity_type='meter' AND e.is_archived=0
        ORDER BY e.name
        """,
        (chat_id, area_id),
    )
    return [Entity(int(r["id"]), int(r["chat_id"]), r["entity_type"], r["name"], r["normalized"], r["default_unit"]) for r in rows]



def get_job_permissions(chat_id: int, job_id: int | None) -> dict[str, bool]:
    chat_id = resolve_scope_chat_id(chat_id)
    if not job_id:
        return {}
    row = db.fetchone("SELECT permissions_json FROM job_titles WHERE chat_id=? AND id=? AND is_archived=0", (chat_id, job_id))
    if not row:
        return {}
    try:
        return json.loads(row["permissions_json"] or "{}")
    except Exception:
        return {}


def set_worker_job(chat_id: int, user_id: int, display_name: str, job_id: int | None) -> None:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    db.execute(
        """
        INSERT INTO workers(chat_id,user_id,display_name,job_title_id,is_active)
        VALUES(?,?,?,?,1)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
            display_name=excluded.display_name,
            job_title_id=excluded.job_title_id,
            is_active=1
        """,
        (scope_chat_id, user_id, display_name or str(user_id), job_id),
    )
    account = get_account_by_scope(scope_chat_id)
    if account:
        grant_account_user_access(account.id, user_id, job_id)


def create_or_set_self_job(chat_id: int, user_id: int, display_name: str, job_name: str) -> tuple[bool, str]:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    job = find_job_title(chat_id, job_name)
    if not job:
        ok, msg = create_job_title(chat_id, job_name, full_permissions())
        if not ok:
            return False, msg
        job = find_job_title(chat_id, job_name)
    if not job:
        return False, "Должность не найдена."
    set_worker_job(chat_id, user_id, display_name, int(job["id"]))
    account = get_account_by_scope(scope_chat_id)
    if account:
        grant_account_user_access(account.id, user_id, int(job["id"]), display_manage=True)
    return True, f"Ваша должность здесь: {job['name']}"


def get_worker(chat_id: int, user_id: int) -> dict | None:
    chat_id = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT * FROM workers WHERE chat_id=? AND user_id=? AND is_active=1", (chat_id, user_id))
    return dict(row) if row else None


def worker_permissions(chat_id: int, user_id: int) -> dict[str, bool]:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    worker = get_worker(scope_chat_id, user_id)
    if not worker:
        return {}
    return get_job_permissions(scope_chat_id, worker.get("job_title_id"))


def list_workers(chat_id: int) -> list[dict]:
    chat_id = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT w.user_id,w.display_name,j.name AS job_name,w.is_active,w.created_at
        FROM workers w
        LEFT JOIN job_titles j ON j.id=w.job_title_id
        WHERE w.chat_id=? AND w.is_active=1
        ORDER BY w.display_name
        """,
        (chat_id,),
    )
    return [dict(r) for r in rows]


def set_product_components(chat_id: int, product_id: int, components: list[tuple[int, float]]) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    product = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='product' AND is_archived=0", (chat_id, product_id))
    if not product:
        return
    db.execute("DELETE FROM product_components WHERE product_id=?", (product_id,))
    for component_id, qty in components:
        comp = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='component' AND is_archived=0", (chat_id, component_id))
        if comp and qty > 0:
            db.execute("INSERT OR REPLACE INTO product_components(product_id,component_id,quantity) VALUES(?,?,?)", (product_id, component_id, float(qty)))


def list_product_components(product_id: int) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT pc.component_id,pc.quantity,e.name,e.default_unit
        FROM product_components pc
        JOIN entities e ON e.id=pc.component_id
        WHERE pc.product_id=? AND e.is_archived=0
        ORDER BY e.name
        """,
        (product_id,),
    )
    return [dict(r) for r in rows]


def inventory_quantity(chat_id: int, entity_type: str, entity_id: int, unit: str = "шт", area_id: int | None = None) -> float:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(quantity),0) AS qty
        FROM inventory
        WHERE chat_id=? AND entity_type=? AND entity_id=? AND unit=?
          AND ((area_id IS NULL AND ? IS NULL) OR area_id=?)
        """,
        (scope_chat_id, entity_type, entity_id, unit, area_id, area_id),
    )
    return float(row["qty"] if row else 0)


def _count_table(table: str, where: str = "", params: tuple = ()) -> int:
    query = f"SELECT COUNT(*) AS n FROM {table} " + where
    row = db.fetchone(query, params)
    return int(row["n"] if row else 0)


def owner_global_stats() -> dict[str, object]:
    from ..config import settings

    last = db.fetchone("SELECT created_at FROM operations ORDER BY created_at DESC LIMIT 1")
    try:
        size = settings.database_path.stat().st_size if settings.database_path.exists() else 0
    except OSError:
        size = 0
    if size >= 1024 * 1024:
        size_text = f"{size / (1024 * 1024):.2f} МБ"
    elif size >= 1024:
        size_text = f"{size / 1024:.1f} КБ"
    else:
        size_text = f"{size} Б"
    return {
        "total_chats": _count_table("chats"),
        "connected_chats": _count_table("chats", "WHERE is_connected=1"),
        "private_chats": _count_table("chats", "WHERE chat_type='private'"),
        "group_chats": _count_table("chats", "WHERE chat_type IN ('group','supergroup')"),
        "areas": _count_table("areas", "WHERE is_archived=0"),
        "job_titles": _count_table("job_titles", "WHERE is_archived=0"),
        "entities": _count_table("entities", "WHERE is_archived=0"),
        "aliases": _count_table("aliases"),
        "lexicon": _count_table("local_lexicon"),
        "operations": _count_table("operations"),
        "pending": _count_table("pending_confirmations"),
        "inventory_rows": _count_table("inventory"),
        "accounts": _count_table("accounting_accounts", "WHERE is_archived=0"),
        "account_links": _count_table("account_chat_access"),
        "account_users": _count_table("account_user_access"),
        "last_operation_at": last["created_at"] if last else None,
        "database_path": str(settings.database_path),
        "database_size": size_text,
    }


def owner_list_chats(limit: int = 50) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT c.chat_id,c.title,c.chat_type,c.is_connected,c.updated_at,
               COUNT(o.id) AS operations_count
        FROM chats c
        LEFT JOIN operations o ON o.group_chat_id=c.chat_id
        GROUP BY c.chat_id
        ORDER BY c.is_connected DESC, c.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]


def owner_chat_report(chat_id: int) -> str:
    chat = db.fetchone("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
    if not chat:
        return "Чат не найден."
    areas = _count_table("areas", "WHERE chat_id=? AND is_archived=0", (chat_id,))
    jobs = _count_table("job_titles", "WHERE chat_id=? AND is_archived=0", (chat_id,))
    entities_count = _count_table("entities", "WHERE chat_id=? AND is_archived=0", (chat_id,))
    aliases_count = _count_table("aliases", "WHERE chat_id=?", (chat_id,))
    lexicon_count = _count_table("local_lexicon", "WHERE chat_id=?", (chat_id,))
    operations_count = _count_table("operations", "WHERE group_chat_id=? OR chat_id=?", (chat_id, chat_id))
    inventory_count = _count_table("inventory", "WHERE chat_id=?", (chat_id,))
    bound = get_bound_area(chat_id)
    active_account = get_active_account(chat_id)
    title = chat["title"] or str(chat_id)
    connected = "подключена" if chat["is_connected"] else "не подключена"
    bound_text = bound.name if bound else "не выбран"
    last = db.fetchone("SELECT created_at FROM operations WHERE group_chat_id=? OR chat_id=? ORDER BY created_at DESC LIMIT 1", (chat_id, chat_id))
    return (
        f"Чат: {title}\n\n"
        f"ID: {chat_id}\n"
        f"Тип: {chat['chat_type'] or 'не указан'}\n"
        f"Состояние: {connected}\n"
        f"Участок группы: {bound_text}\n"
        f"Активный учёт: {(active_account.name if active_account else 'учёт группы')}\n"
        f"Участков: {areas}\n"
        f"Должностей: {jobs}\n"
        f"Позиций: {entities_count}\n"
        f"Сокращений: {aliases_count}\n"
        f"Локальных слов: {lexicon_count}\n"
        f"Операций: {operations_count}\n"
        f"Строк склада: {inventory_count}\n"
        f"Последняя активность: {(last['created_at'] if last else 'нет данных')}"
    )


def owner_list_accounts(limit: int = 200) -> list[AccountingAccount]:
    rows = db.fetchall(
        "SELECT * FROM accounting_accounts WHERE is_archived=0 ORDER BY is_general DESC, created_at DESC LIMIT ?",
        (limit,),
    )
    return [_account_from_row(r) for r in rows]


def owner_account_report(account_id: int) -> str:
    row = db.fetchone("SELECT * FROM accounting_accounts WHERE id=? AND is_archived=0", (account_id,))
    if not row:
        return "Учёт не найден."
    account = _account_from_row(row)
    chats = list_account_chats(account.id)
    scope = account.scope_chat_id
    areas = _count_table("areas", "WHERE chat_id=? AND is_archived=0", (scope,))
    jobs = _count_table("job_titles", "WHERE chat_id=? AND is_archived=0", (scope,))
    entities_count = _count_table("entities", "WHERE chat_id=? AND is_archived=0", (scope,))
    operations_count = _count_table("operations", "WHERE chat_id=?", (scope,))
    inventory_count = _count_table("inventory", "WHERE chat_id=?", (scope,))
    users_count = _count_table("account_user_access", "WHERE account_id=?", (account.id,))
    common = "да" if account.is_general else "нет"
    chat_lines = []
    for ch in chats[:20]:
        title = ch.get("title") or str(ch.get("chat_id"))
        chat_lines.append(f"• {title}")
    if not chat_lines:
        chat_lines.append("• нет подключённых чатов")
    return (
        f"Учёт: {account.name}\n\n"
        f"Общий: {common}\n"
        f"Подключённых чатов: {len(chats)}\n"
        f"Участков: {areas}\n"
        f"Должностей: {jobs}\n"
        f"Позиций: {entities_count}\n"
        f"Операций: {operations_count}\n"
        f"Строк склада: {inventory_count}\n"
        f"Пользователей с доступом: {users_count}\n\n"
        "Чаты:\n" + "\n".join(chat_lines)
    )

EXPORT_SECTION_KEYS = {
    "inventory": "Склад",
    "period_totals": "Итоги за период",
    "daily_matrix": "По датам",
    "capacity": "Расчёт сборки",
    "journal": "Журнал",
}


def default_export_preferences() -> dict[str, bool]:
    return {key: True for key in EXPORT_SECTION_KEYS}


def get_export_preferences(chat_id: int, user_id: int | None) -> dict[str, bool]:
    prefs = default_export_preferences()
    if not user_id:
        return prefs
    scope_chat_id = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT * FROM export_preferences WHERE chat_id=? AND user_id=?", (scope_chat_id, user_id))
    if not row:
        return prefs
    prefs["inventory"] = bool(row["include_inventory"])
    prefs["period_totals"] = bool(row["include_period_totals"])
    prefs["daily_matrix"] = bool(row["include_daily_matrix"])
    prefs["capacity"] = bool(row["include_capacity"])
    prefs["journal"] = bool(row["include_journal"])
    return prefs


def set_export_preference(chat_id: int, user_id: int, section_key: str, enabled: bool) -> None:
    if section_key not in EXPORT_SECTION_KEYS:
        return
    scope_chat_id = resolve_scope_chat_id(chat_id)
    current = get_export_preferences(scope_chat_id, user_id)
    current[section_key] = enabled
    db.execute(
        """
        INSERT INTO export_preferences(
            chat_id,user_id,include_inventory,include_period_totals,include_daily_matrix,include_capacity,include_journal,updated_at
        ) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
            include_inventory=excluded.include_inventory,
            include_period_totals=excluded.include_period_totals,
            include_daily_matrix=excluded.include_daily_matrix,
            include_capacity=excluded.include_capacity,
            include_journal=excluded.include_journal,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            scope_chat_id,
            user_id,
            1 if current["inventory"] else 0,
            1 if current["period_totals"] else 0,
            1 if current["daily_matrix"] else 0,
            1 if current["capacity"] else 0,
            1 if current["journal"] else 0,
        ),
    )


def set_export_preferences(chat_id: int, user_id: int, prefs: dict[str, bool]) -> None:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    current = default_export_preferences()
    for key in current:
        current[key] = bool(prefs.get(key, False))
    db.execute(
        """
        INSERT INTO export_preferences(
            chat_id,user_id,include_inventory,include_period_totals,include_daily_matrix,include_capacity,include_journal,updated_at
        ) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
            include_inventory=excluded.include_inventory,
            include_period_totals=excluded.include_period_totals,
            include_daily_matrix=excluded.include_daily_matrix,
            include_capacity=excluded.include_capacity,
            include_journal=excluded.include_journal,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            scope_chat_id,
            user_id,
            1 if current["inventory"] else 0,
            1 if current["period_totals"] else 0,
            1 if current["daily_matrix"] else 0,
            1 if current["capacity"] else 0,
            1 if current["journal"] else 0,
        ),
    )


def format_export_preferences(chat_id: int, user_id: int | None) -> str:
    prefs = get_export_preferences(chat_id, user_id)
    lines = ["Разделы отчёта", "", "Отметьте, что включить:"]
    for key, label in EXPORT_SECTION_KEYS.items():
        mark = "✅" if prefs.get(key) else "⬜"
        lines.append(f"{mark} {label}")
    return "\n".join(lines)


def all_products_with_components(chat_id: int) -> list[dict]:
    scope_chat_id = resolve_scope_chat_id(chat_id)
    products = list_entities(scope_chat_id, {"product"})
    result: list[dict] = []
    for product in products:
        result.append({"product": product, "components": list_product_components(product.id)})
    return result
