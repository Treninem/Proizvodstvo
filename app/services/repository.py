from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
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
    "movement", "fulfillment", "returns", "reports", "stock",
    "edit", "setup", "workers", "grant", "permissions", "export",
    "site", "sales", "overview", "shifts",
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


def is_primary_owner_id(user_id: int | None) -> bool:
    return bool(user_id and settings.primary_owner_id and int(user_id) == int(settings.primary_owner_id))


def list_system_admin_ids(*, include_owner: bool = True) -> list[int]:
    ids: set[int] = set()
    if include_owner and settings.primary_owner_id:
        ids.add(int(settings.primary_owner_id))
    try:
        rows = db.fetchall("SELECT user_id FROM system_admins WHERE is_active=1 ORDER BY user_id")
        ids.update(int(row["user_id"]) for row in rows)
    except Exception:
        # Таблица создаётся при init_db; до инициализации доступен только владелец из .env.
        pass
    return sorted(ids)


def list_system_admins() -> list[dict]:
    try:
        return [dict(row) for row in db.fetchall(
            "SELECT user_id,display_name,granted_by,created_at,updated_at FROM system_admins WHERE is_active=1 ORDER BY display_name,user_id"
        )]
    except Exception:
        return []


def grant_system_admin(actor_user_id: int, target_user_id: int, display_name: str = "") -> tuple[bool, str]:
    if not is_primary_owner_id(actor_user_id):
        return False, "Назначать полных администраторов может только владелец."
    if int(target_user_id) <= 0:
        return False, "Укажите корректный Telegram ID."
    if is_primary_owner_id(target_user_id):
        return False, "Этот пользователь уже является владельцем."
    db.execute(
        """
        INSERT INTO system_admins(user_id,display_name,granted_by,is_active,updated_at)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            display_name=excluded.display_name,
            granted_by=excluded.granted_by,
            is_active=1,
            updated_at=CURRENT_TIMESTAMP
        """,
        (int(target_user_id), (display_name or "").strip(), int(actor_user_id), 1),
    )
    return True, "Полный административный доступ выдан."


def revoke_system_admin(actor_user_id: int, target_user_id: int) -> tuple[bool, str]:
    if not is_primary_owner_id(actor_user_id):
        return False, "Отзывать полный доступ может только владелец."
    if is_primary_owner_id(target_user_id):
        return False, "Доступ владельца нельзя отключить."
    row = db.fetchone("SELECT user_id FROM system_admins WHERE user_id=? AND is_active=1", (int(target_user_id),))
    if not row:
        return False, "Администратор не найден."
    db.execute(
        "UPDATE system_admins SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (int(target_user_id),),
    )
    return True, "Полный административный доступ отозван."


def is_global_owner_id(user_id: int | None) -> bool:
    """Platform/root owner only. Never implies tenant membership."""
    return is_primary_owner_id(user_id)


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


def clear_active_account(chat_id: int) -> None:
    db.execute("DELETE FROM chat_active_account WHERE chat_id=?", (chat_id,))


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
                # A chat link alone must never reveal another tenant to a user.
                # The user needs explicit tenant access (group creators receive it
                # when their accounting context is initialized).
                if acc.id not in seen and user_has_account_access(acc.id, user_id):
                    accounts.append(acc)
                    seen.add(acc.id)
    # Safe compatibility migration for accounts created after older releases
    # had already stored business data directly under the Telegram group chat_id.
    # The existing repair helper only switches the scope when the synthetic scope
    # is completely empty and the original group scope contains real business rows;
    # if both sides contain data or another account owns the scope, it does nothing.
    repaired_accounts: list[AccountingAccount] = []
    for account in accounts:
        chat_row = db.fetchone("SELECT chat_type FROM chats WHERE chat_id=?", (int(account.owner_chat_id),))
        chat_type = str(chat_row["chat_type"] or "") if chat_row else ""
        if chat_type in {"group", "supergroup"}:
            account = _repair_empty_account_scope_from_group(account, int(account.owner_chat_id))
        repaired_accounts.append(account)
    return repaired_accounts



def _unique_account_name(owner_user_id: int, base_name: str, fallback_id: int) -> str:
    clean = (base_name or '').strip() or 'Учёт группы'
    key = normalize_key(clean)
    row = db.fetchone(
        "SELECT id FROM accounting_accounts WHERE owner_user_id=? AND normalized=? AND is_archived=0",
        (owner_user_id, key),
    )
    if not row:
        return clean
    suffix = str(abs(fallback_id))[-6:]
    candidate = f"{clean} {suffix}"
    idx = 2
    while db.fetchone(
        "SELECT id FROM accounting_accounts WHERE owner_user_id=? AND normalized=? AND is_archived=0",
        (owner_user_id, normalize_key(candidate)),
    ):
        candidate = f"{clean} {suffix}-{idx}"
        idx += 1
    return candidate


_GROUP_SCOPE_DATA_TABLES = (
    "areas", "job_titles", "workers", "entities", "inventory", "operations",
    "departments", "assembly_plan_targets", "material_stock_settings",
    "production_tasks", "interdepartment_requests", "production_lots",
    "equipment", "equipment_downtimes", "maintenance_records",
    "quality_rules", "quality_inspections", "replenishment_settings",
    "replenishment_requests", "maintenance_plans", "maintenance_work_orders",
)


def _scope_business_row_count(chat_id: int) -> int:
    """Count real accounting rows, ignoring access/session/audit metadata."""
    total = 0
    with db.connect() as conn:
        for table in _GROUP_SCOPE_DATA_TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE chat_id=?", (int(chat_id),)).fetchone()
                total += int(row[0] if row else 0)
            except Exception:
                # Older databases may not have the newest tables yet.
                continue
    return total


def _repair_empty_account_scope_from_group(account: AccountingAccount, group_chat_id: int) -> AccountingAccount:
    """Reuse legacy group data when a synthetic account scope is still empty.

    Older releases could write directly under the Telegram group chat_id and later
    create a synthetic account scope. Switching is safe only when the synthetic
    scope has no business rows and the group scope already has real accounting data.
    If both contain data, we deliberately do nothing to avoid an automatic merge.
    """
    group_chat_id = int(group_chat_id)
    if int(account.scope_chat_id) == group_chat_id:
        return account
    occupied = get_account_by_scope(group_chat_id)
    if occupied and int(occupied.id) != int(account.id):
        return account
    try:
        group_rows = _scope_business_row_count(group_chat_id)
        account_rows = _scope_business_row_count(int(account.scope_chat_id))
    except Exception:
        return account
    if group_rows <= 0 or account_rows > 0:
        return account
    try:
        db.execute("UPDATE accounting_accounts SET scope_chat_id=? WHERE id=?", (group_chat_id, int(account.id)))
        repaired = get_account_by_id(int(account.id))
        return repaired or account
    except Exception:
        return account


def ensure_group_account_context(group_chat_id: int, group_title: str, group_type: str, owner_user_id: int, private_chat_id: int | None = None, private_title: str = '') -> AccountingAccount | None:
    """Prepare a real accounting context for setup opened from a private chat.

    Telegram sends inline-button callbacks in the private chat, so setup screens need an
    active account mapped to that private chat. The account itself remains tied to the
    selected group and its data is stored in the account scope, not in the private chat.
    """
    if not owner_user_id:
        return None
    title = (group_title or '').strip() or 'Рабочая группа'
    chat_type = (group_type or '').strip() or 'supergroup'
    upsert_chat(group_chat_id, title, chat_type, connected=True)
    account = get_active_account(group_chat_id)
    if account is None:
        name = _unique_account_name(owner_user_id, title, group_chat_id)
        ok, _msg, account_id = create_account(owner_user_id, group_chat_id, name)
        if not ok or not account_id:
            return None
        attach_chat_to_account(account_id, group_chat_id, can_manage=True, set_active=True)
        account = get_account_by_id(account_id)
    if account is None:
        return None
    account = _repair_empty_account_scope_from_group(account, group_chat_id)
    grant_account_user_access(account.id, owner_user_id, None, display_manage=True)
    attach_chat_to_account(account.id, group_chat_id, can_manage=True, set_active=True)
    if private_chat_id is not None:
        upsert_chat(private_chat_id, private_title or 'Личный чат', 'private', connected=True)
        attach_chat_to_account(account.id, private_chat_id, can_manage=True, set_active=True)
    return account


def ensure_private_account_context(user_id: int, private_chat_id: int, private_title: str = '') -> AccountingAccount | None:
    """Let a first-time user configure a fresh account in private without a false denial."""
    if not user_id:
        return None
    upsert_chat(private_chat_id, private_title or 'Личный чат', 'private', connected=True)
    account = get_active_account(private_chat_id)
    if account and user_has_account_access(account.id, user_id, require_manage=True):
        return account
    accounts = list_accounts_for_user(user_id, private_chat_id, include_accessible=True)
    if accounts:
        account = accounts[0]
        grant_account_user_access(account.id, user_id, None, display_manage=True)
        attach_chat_to_account(account.id, private_chat_id, can_manage=True, set_active=True)
        return account
    name = _unique_account_name(user_id, 'Учёт', private_chat_id)
    ok, _msg, account_id = create_account(user_id, private_chat_id, name)
    if not ok or not account_id:
        return None
    grant_account_user_access(account_id, user_id, None, display_manage=True)
    attach_chat_to_account(account_id, private_chat_id, can_manage=True, set_active=True)
    return get_account_by_id(account_id)

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
    # Ordinary bot/Mini App access is always tenant-scoped, even for platform owner.
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
    account = get_active_account(chat_id)
    if account:
        return user_has_account_access(account.id, user_id, require_manage=True)
    permissions = worker_permissions(chat_id, user_id or 0)
    return bool(permissions.get("setup") or permissions.get("workers") or permissions.get("grant") or permissions.get("permissions"))


def user_permissions_current_context(chat_id: int, user_id: int | None) -> dict[str, bool]:
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


def _normalize_entity_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").strip().upper() if not ch.isspace())[:120]


def set_entity_code(chat_id: int, entity_id: int, code: str, created_by: int | None = None) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    entity = get_entity(int(entity_id))
    normalized = _normalize_entity_code(code)
    if not entity or int(entity.chat_id) != int(scope):
        return False, "Позиция не найдена.", None
    if len(normalized) < 2:
        return False, "Код должен содержать не менее двух символов.", None
    conflict = db.fetchone("SELECT entity_id FROM entity_codes WHERE chat_id=? AND normalized=?", (scope, normalized))
    if conflict and int(conflict["entity_id"]) != int(entity_id):
        return False, "Этот код уже назначен другой позиции.", None
    with db.connect() as conn:
        conn.execute("UPDATE entity_codes SET is_primary=0,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND entity_id=?", (scope, int(entity_id)))
        existing = conn.execute("SELECT id FROM entity_codes WHERE chat_id=? AND normalized=?", (scope, normalized)).fetchone()
        if existing:
            code_id = int(existing["id"])
            conn.execute("UPDATE entity_codes SET code=?,entity_id=?,is_primary=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(code).strip()[:120], int(entity_id), code_id))
        else:
            cur = conn.execute(
                "INSERT INTO entity_codes(chat_id,entity_id,code,normalized,is_primary,created_by) VALUES(?,?,?,?,1,?)",
                (scope, int(entity_id), str(code).strip()[:120], normalized, int(created_by) if created_by else None),
            )
            code_id = int(cur.lastrowid)
        conn.commit()
    return True, "Код позиции сохранён.", code_id


def delete_entity_code(chat_id: int, code_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT entity_id,is_primary FROM entity_codes WHERE chat_id=? AND id=?", (scope, int(code_id)))
    if not row:
        return False
    db.execute("DELETE FROM entity_codes WHERE chat_id=? AND id=?", (scope, int(code_id)))
    if int(row["is_primary"] or 0):
        replacement = db.fetchone("SELECT id FROM entity_codes WHERE chat_id=? AND entity_id=? ORDER BY id LIMIT 1", (scope, int(row["entity_id"])))
        if replacement:
            db.execute("UPDATE entity_codes SET is_primary=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(replacement["id"]),))
    return True


def list_entity_codes(chat_id: int, entity_ids: Iterable[int] | None = None) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    params: list[object] = [scope]
    where = ["ec.chat_id=?"]
    if entity_ids is not None:
        ids = sorted({int(value) for value in entity_ids if int(value) > 0})
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        where.append(f"ec.entity_id IN ({marks})")
        params.extend(ids)
    rows = db.fetchall(
        f"""SELECT ec.*,e.name AS entity_name,e.entity_type,e.default_unit
            FROM entity_codes ec JOIN entities e ON e.id=ec.entity_id AND e.is_archived=0
            WHERE {' AND '.join(where)}
            ORDER BY e.name,ec.is_primary DESC,ec.code""",
        tuple(params),
    )
    return [dict(row) for row in rows]


def resolve_entity_code(chat_id: int, code: str) -> dict | None:
    scope = resolve_scope_chat_id(chat_id)
    normalized = _normalize_entity_code(code)
    if not normalized:
        return None
    row = db.fetchone(
        """SELECT ec.*,e.name AS entity_name,e.entity_type,e.default_unit
           FROM entity_codes ec JOIN entities e ON e.id=ec.entity_id AND e.is_archived=0
           WHERE ec.chat_id=? AND ec.normalized=?""",
        (scope, normalized),
    )
    return dict(row) if row else None


def primary_entity_code(entity_id: int) -> str:
    row = db.fetchone("SELECT code FROM entity_codes WHERE entity_id=? ORDER BY is_primary DESC,id LIMIT 1", (int(entity_id),))
    return str(row["code"]) if row else ""


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




def copy_job_titles_between_contexts(source_chat_id: int, target_chat_id: int) -> int:
    """Copy visible job titles from one accounting context to another without overwriting existing names."""
    source_scope = resolve_scope_chat_id(source_chat_id)
    target_scope = resolve_scope_chat_id(target_chat_id)
    if source_scope == target_scope:
        return 0
    source_rows = db.fetchall(
        "SELECT name,normalized,permissions_json FROM job_titles WHERE chat_id=? AND is_archived=0 ORDER BY name",
        (source_scope,),
    )
    if not source_rows:
        return 0
    copied = 0
    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for row in source_rows:
            exists = conn.execute(
                "SELECT id FROM job_titles WHERE chat_id=? AND normalized=? AND is_archived=0",
                (target_scope, row["normalized"]),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO job_titles(chat_id,name,normalized,permissions_json) VALUES(?,?,?,?)",
                (target_scope, row["name"], row["normalized"], row["permissions_json"] or "{}"),
            )
            copied += 1
        conn.commit()
    return copied

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



# --- Настраиваемый план сборки ---

def set_assembly_plan_targets(chat_id: int, product_id: int, targets: list[int | float]) -> int:
    scope = resolve_scope_chat_id(chat_id)
    product = get_entity(product_id)
    if not product or product.chat_id != scope or product.entity_type != "product":
        return 0
    saved = 0
    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for raw in targets:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            # При изменении цели уведомление начинается заново только для новой цели.
            conn.execute(
                """
                INSERT INTO assembly_plan_targets(chat_id,product_id,target_qty,is_archived,is_notified,updated_at)
                VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id,product_id,target_qty) DO UPDATE SET
                    is_archived=0,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (scope, int(product_id), value, 0),
            )
            saved += 1
        conn.commit()
    return saved


def list_assembly_plan_targets(chat_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT p.id,p.chat_id,p.product_id,p.target_qty,p.is_notified,e.name AS product_name,e.default_unit
        FROM assembly_plan_targets p
        JOIN entities e ON e.id=p.product_id
        WHERE p.chat_id=? AND p.is_archived=0 AND e.is_archived=0
        ORDER BY e.name,p.target_qty
        """,
        (scope,),
    )
    return [dict(r) for r in rows]


def list_assembly_plan_products(chat_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT DISTINCT e.id,e.name
        FROM assembly_plan_targets p
        JOIN entities e ON e.id=p.product_id
        WHERE p.chat_id=? AND p.is_archived=0 AND e.is_archived=0
        ORDER BY e.name
        """,
        (scope,),
    )
    return [dict(r) for r in rows]


def clear_assembly_plan(chat_id: int) -> int:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall("SELECT id FROM assembly_plan_targets WHERE chat_id=? AND is_archived=0", (scope,))
    db.execute("UPDATE assembly_plan_targets SET is_archived=1,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND is_archived=0", (scope,))
    return len(rows)


def clear_assembly_plan_product(chat_id: int, product_id: int) -> int:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall("SELECT id FROM assembly_plan_targets WHERE chat_id=? AND product_id=? AND is_archived=0", (scope, int(product_id)))
    db.execute(
        "UPDATE assembly_plan_targets SET is_archived=1,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND product_id=? AND is_archived=0",
        (scope, int(product_id)),
    )
    return len(rows)


def mark_assembly_plan_notified(plan_id: int) -> None:
    db.execute("UPDATE assembly_plan_targets SET is_notified=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(plan_id),))


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


def add_or_update_product_components(chat_id: int, product_id: int, components: list[tuple[int, float]]) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    product = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='product' AND is_archived=0", (chat_id, product_id))
    if not product:
        return
    for component_id, qty in components:
        comp = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='component' AND is_archived=0", (chat_id, component_id))
        if comp and qty > 0:
            db.execute(
                "INSERT OR REPLACE INTO product_components(product_id,component_id,quantity) VALUES(?,?,?)",
                (product_id, component_id, float(qty)),
            )


def remove_product_components(chat_id: int, product_id: int, component_ids: list[int]) -> int:
    chat_id = resolve_scope_chat_id(chat_id)
    if not component_ids:
        return 0
    product = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='product' AND is_archived=0", (chat_id, product_id))
    if not product:
        return 0
    removed = 0
    for component_id in component_ids:
        before = db.fetchone("SELECT component_id FROM product_components WHERE product_id=? AND component_id=?", (product_id, component_id))
        if before:
            db.execute("DELETE FROM product_components WHERE product_id=? AND component_id=?", (product_id, component_id))
            removed += 1
    return removed


def update_product_component_quantity(chat_id: int, product_id: int, component_id: int, quantity: float) -> bool:
    chat_id = resolve_scope_chat_id(chat_id)
    if quantity <= 0:
        return False
    product = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='product' AND is_archived=0", (chat_id, product_id))
    comp = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND id=? AND entity_type='component' AND is_archived=0", (chat_id, component_id))
    if not product or not comp:
        return False
    existing = db.fetchone("SELECT component_id FROM product_components WHERE product_id=? AND component_id=?", (product_id, component_id))
    if existing:
        db.execute("UPDATE product_components SET quantity=? WHERE product_id=? AND component_id=?", (float(quantity), product_id, component_id))
    else:
        db.execute("INSERT INTO product_components(product_id,component_id,quantity) VALUES(?,?,?)", (product_id, component_id, float(quantity)))
    return True


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


def list_known_group_chats(limit: int = 200) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT chat_id,title,chat_type,is_connected,updated_at
        FROM chats
        WHERE chat_type IN ('group','supergroup')
        ORDER BY is_connected DESC, updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]


def user_has_manage_access_to_chat(chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    rows = db.fetchall(
        """
        SELECT a.id
        FROM account_chat_access ac
        JOIN accounting_accounts a ON a.id=ac.account_id
        WHERE ac.chat_id=? AND a.is_archived=0
        """,
        (chat_id,),
    )
    for row in rows:
        if user_has_account_access(int(row["id"]), user_id, require_manage=True):
            return True
    return bool(user_can_manage_current_context(chat_id, user_id))


def get_chat_info(chat_id: int) -> dict | None:
    row = db.fetchone("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
    return dict(row) if row else None


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
        f"Номер учёта: №{account.id}\n"
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


def set_user_test_mode(user_id: int, enabled: bool) -> None:
    if not is_primary_owner_id(user_id):
        return
    db.execute(
        """
        INSERT INTO user_test_modes(user_id,is_enabled,updated_at)
        VALUES(?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            is_enabled=excluded.is_enabled,
            updated_at=CURRENT_TIMESTAMP
        """,
        (user_id, 1 if enabled else 0),
    )


def is_user_test_mode_enabled(user_id: int | None) -> bool:
    if not is_primary_owner_id(user_id):
        return False
    row = db.fetchone("SELECT is_enabled FROM user_test_modes WHERE user_id=?", (user_id,))
    return bool(row and row["is_enabled"])


def toggle_user_test_mode(user_id: int) -> bool:
    enabled = not is_user_test_mode_enabled(user_id)
    set_user_test_mode(user_id, enabled)
    return enabled

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

# --- Расширенный производственный контур step58 ---

EXTENDED_PERMISSION_KEYS = {"movement", "fulfillment", "returns"}
PERMISSION_KEYS.update(EXTENDED_PERMISSION_KEYS)


def create_destination(chat_id: int, name: str, destination_type: str = "storage") -> tuple[bool, str]:
    chat_id = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    destination_type = (destination_type or "storage").strip() or "storage"
    try:
        db.execute(
            "INSERT INTO operation_destinations(chat_id,name,normalized,destination_type) VALUES(?,?,?,?)",
            (chat_id, name.strip(), key, destination_type),
        )
        return True, f"Место создано: {name.strip()}"
    except Exception:
        return False, "Такое место уже есть."


def list_destinations(chat_id: int, destination_types: set[str] | None = None) -> list[dict]:
    chat_id = resolve_scope_chat_id(chat_id)
    if destination_types:
        marks = ",".join("?" for _ in destination_types)
        rows = db.fetchall(
            f"SELECT * FROM operation_destinations WHERE chat_id=? AND destination_type IN ({marks}) AND is_archived=0 ORDER BY name",
            (chat_id, *sorted(destination_types)),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM operation_destinations WHERE chat_id=? AND is_archived=0 ORDER BY destination_type,name",
            (chat_id,),
        )
    return [dict(r) for r in rows]


def set_material_stock_settings(chat_id: int, material_id: int, min_work_days: float = 5, average_days: int = 14) -> None:
    chat_id = resolve_scope_chat_id(chat_id)
    db.execute(
        """
        INSERT OR REPLACE INTO material_stock_settings(chat_id,material_id,min_work_days,average_days,updated_at)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (chat_id, int(material_id), float(min_work_days), int(average_days)),
    )


def get_material_stock_settings(chat_id: int, material_id: int) -> dict:
    chat_id = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT * FROM material_stock_settings WHERE chat_id=? AND material_id=?",
        (chat_id, int(material_id)),
    )
    if not row:
        return {"min_work_days": 5.0, "average_days": 14}
    return dict(row)


# --- Web / Mini App audit ---

def log_site_action(chat_id: int | None, user_id: int | None, action: str, details: str = "") -> None:
    db.execute(
        "INSERT INTO site_access_log(chat_id,user_id,action,details) VALUES(?,?,?,?)",
        (chat_id, user_id, action, details[:500]),
    )


def log_sync_event(chat_id: int | None, source: str, status: str, details: str = "") -> None:
    db.execute(
        "INSERT INTO sync_events(chat_id,source,status,details) VALUES(?,?,?,?)",
        (chat_id, source[:50], status[:50], details[:500]),
    )


def list_site_actions(chat_id: int, limit: int = 12) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT id,chat_id,user_id,action,details,created_at
        FROM site_access_log
        WHERE chat_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (scope, int(limit)),
    )
    return [dict(r) for r in rows]


def list_sync_events(chat_id: int, limit: int = 12) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT id,chat_id,source,status,details,created_at
        FROM sync_events
        WHERE chat_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (scope, int(limit)),
    )
    return [dict(r) for r in rows]

# --- Площадки, места хранения и доступы сайта step64 ---

AREA_SECTION_KEYS = {
    "overview",
    "production",
    "material",
    "assembly",
    "movement",
    "shipment",
    "returns",
    "reports",
    "inventory",
}


def get_destination(chat_id: int, destination_id: int) -> dict | None:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT * FROM operation_destinations WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(destination_id)),
    )
    return dict(row) if row else None


def update_destination(chat_id: int, destination_id: int, name: str, destination_type: str = "storage") -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    destination_type = (destination_type or "storage").strip() or "storage"
    row = db.fetchone(
        "SELECT id FROM operation_destinations WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(destination_id)),
    )
    if not row:
        return False, "Место не найдено."
    conflict = db.fetchone(
        "SELECT id FROM operation_destinations WHERE chat_id=? AND normalized=? AND id<>? AND is_archived=0",
        (scope, key, int(destination_id)),
    )
    if conflict:
        return False, "Такое место уже есть."
    db.execute(
        "UPDATE operation_destinations SET name=?,normalized=?,destination_type=? WHERE chat_id=? AND id=?",
        (name.strip(), key, destination_type, scope, int(destination_id)),
    )
    return True, f"Место обновлено: {name.strip()}"


def archive_destination(chat_id: int, destination_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id FROM operation_destinations WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(destination_id)),
    )
    if not row:
        return False
    db.execute(
        "UPDATE operation_destinations SET is_archived=1 WHERE chat_id=? AND id=?",
        (scope, int(destination_id)),
    )
    return True


def current_user_job_title_id(chat_id: int, user_id: int | None) -> int | None:
    if not user_id:
        return None
    scope = resolve_scope_chat_id(chat_id)
    account = get_account_by_scope(scope)
    if account:
        row = db.fetchone(
            "SELECT job_title_id FROM account_user_access WHERE account_id=? AND user_id=?",
            (account.id, int(user_id)),
        )
        if row and row["job_title_id"]:
            return int(row["job_title_id"])
    row = db.fetchone(
        "SELECT job_title_id FROM workers WHERE chat_id=? AND user_id=? AND is_active=1",
        (scope, int(user_id)),
    )
    return int(row["job_title_id"]) if row and row["job_title_id"] else None


def list_area_section_access(chat_id: int, job_title_id: int | None = None) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["asa.chat_id=?", "j.is_archived=0", "a.is_archived=0"]
    params: list[object] = [scope]
    if job_title_id is not None:
        where.append("asa.job_title_id=?")
        params.append(int(job_title_id))
    rows = db.fetchall(
        f"""
        SELECT asa.chat_id,asa.job_title_id,j.name AS job_title_name,
               asa.area_id,a.name AS area_name,asa.section_key,
               asa.can_view,asa.can_submit,asa.can_edit,
               asa.created_at,asa.updated_at
        FROM area_section_access asa
        JOIN job_titles j ON j.id=asa.job_title_id AND j.chat_id=asa.chat_id
        JOIN areas a ON a.id=asa.area_id AND a.chat_id=asa.chat_id
        WHERE {' AND '.join(where)}
        ORDER BY j.name,a.name,asa.section_key
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def set_area_section_access(
    chat_id: int,
    job_title_id: int,
    area_id: int,
    section_key: str,
    *,
    can_view: bool = True,
    can_submit: bool = False,
    can_edit: bool = False,
) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    section = (section_key or "").strip()
    if section not in AREA_SECTION_KEYS:
        return False
    job = db.fetchone(
        "SELECT id FROM job_titles WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(job_title_id)),
    )
    area = db.fetchone(
        "SELECT id FROM areas WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(area_id)),
    )
    if not job or not area:
        return False
    # Отправка и редактирование автоматически подразумевают просмотр.
    view_value = bool(can_view or can_submit or can_edit)
    submit_value = bool(can_submit or can_edit)
    db.execute(
        """
        INSERT INTO area_section_access(
            chat_id,job_title_id,area_id,section_key,can_view,can_submit,can_edit,updated_at
        ) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id,job_title_id,area_id,section_key) DO UPDATE SET
            can_view=excluded.can_view,
            can_submit=excluded.can_submit,
            can_edit=excluded.can_edit,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            scope,
            int(job_title_id),
            int(area_id),
            section,
            1 if view_value else 0,
            1 if submit_value else 0,
            1 if can_edit else 0,
        ),
    )
    return True


def delete_area_section_access(chat_id: int, job_title_id: int, area_id: int, section_key: str) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT section_key FROM area_section_access
        WHERE chat_id=? AND job_title_id=? AND area_id=? AND section_key=?
        """,
        (scope, int(job_title_id), int(area_id), (section_key or "").strip()),
    )
    if not row:
        return False
    db.execute(
        "DELETE FROM area_section_access WHERE chat_id=? AND job_title_id=? AND area_id=? AND section_key=?",
        (scope, int(job_title_id), int(area_id), (section_key or "").strip()),
    )
    return True


def area_section_access_for_user(chat_id: int, user_id: int | None, section_key: str) -> dict[str, object]:
    scope = resolve_scope_chat_id(chat_id)
    account = get_account_by_scope(scope)
    if is_tenant_admin(scope, user_id):
        return {"restricted": False, "view": None, "submit": None, "edit": None}
    if account and user_id and account.owner_user_id == int(user_id):
        return {"restricted": False, "view": None, "submit": None, "edit": None}
    job_id = current_user_job_title_id(scope, user_id)
    if not job_id:
        return {"restricted": False, "view": None, "submit": None, "edit": None}
    rows = db.fetchall(
        """
        SELECT area_id,can_view,can_submit,can_edit
        FROM area_section_access
        WHERE chat_id=? AND job_title_id=? AND section_key=?
        """,
        (scope, int(job_id), (section_key or "").strip()),
    )
    if not rows:
        return {"restricted": False, "view": None, "submit": None, "edit": None}
    view = {int(row["area_id"]) for row in rows if row["can_view"]}
    submit = {int(row["area_id"]) for row in rows if row["can_submit"]}
    edit = {int(row["area_id"]) for row in rows if row["can_edit"]}
    return {"restricted": True, "view": view, "submit": submit, "edit": edit}


def user_area_action_allowed(
    chat_id: int,
    user_id: int | None,
    section_key: str,
    area_id: int | None,
    action: str = "view",
) -> bool:
    access = area_section_access_for_user(chat_id, user_id, section_key)
    if not access.get("restricted"):
        return True
    if area_id is None:
        return False
    allowed = access.get(action)
    return bool(isinstance(allowed, set) and int(area_id) in allowed)


def area_access_map_for_user(chat_id: int, user_id: int | None) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for section in sorted(AREA_SECTION_KEYS):
        access = area_section_access_for_user(chat_id, user_id, section)
        result[section] = {
            "restricted": bool(access.get("restricted")),
            "view": sorted(access.get("view") or []) if access.get("restricted") else [],
            "submit": sorted(access.get("submit") or []) if access.get("restricted") else [],
            "edit": sorted(access.get("edit") or []) if access.get("restricted") else [],
        }
    return result

# Учитываем прямой scope учёта при обращении сайта.
def user_can_manage_current_context(chat_id: int, user_id: int | None) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    account = get_active_account(chat_id) or get_account_by_scope(scope)
    if account:
        return user_has_account_access(account.id, user_id, require_manage=True)
    permissions = worker_permissions(scope, user_id or 0)
    return bool(permissions.get("setup") or permissions.get("workers") or permissions.get("grant") or permissions.get("permissions"))


def user_permissions_current_context(chat_id: int, user_id: int | None) -> dict[str, bool]:
    scope = resolve_scope_chat_id(chat_id)
    account = get_active_account(chat_id) or get_account_by_scope(scope)
    if account and user_id:
        if account.owner_user_id == int(user_id):
            return full_permissions()
        row = db.fetchone(
            "SELECT job_title_id,can_manage,can_view,can_submit FROM account_user_access WHERE account_id=? AND user_id=?",
            (account.id, int(user_id)),
        )
        if row:
            return _permissions_from_job_id(int(row["job_title_id"]) if row["job_title_id"] else None)
    return worker_permissions(scope, user_id or 0)

# Удалённое место не блокирует повторное использование его названия.
def create_destination(chat_id: int, name: str, destination_type: str = "storage") -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    destination_type = (destination_type or "storage").strip() or "storage"
    existing = db.fetchone(
        "SELECT id,is_archived FROM operation_destinations WHERE chat_id=? AND normalized=?",
        (scope, key),
    )
    if existing and not int(existing["is_archived"] or 0):
        return False, "Такое место уже есть."
    if existing:
        db.execute(
            "UPDATE operation_destinations SET name=?,destination_type=?,is_archived=0 WHERE chat_id=? AND id=?",
            (name.strip(), destination_type, scope, int(existing["id"])),
        )
        return True, f"Место создано: {name.strip()}"
    db.execute(
        "INSERT INTO operation_destinations(chat_id,name,normalized,destination_type) VALUES(?,?,?,?)",
        (scope, name.strip(), key, destination_type),
    )
    return True, f"Место создано: {name.strip()}"


def archive_destination(chat_id: int, destination_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id,normalized FROM operation_destinations WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(destination_id)),
    )
    if not row:
        return False
    archived_key = f"{row['normalized']} archived {int(destination_id)}"
    db.execute(
        "UPDATE operation_destinations SET is_archived=1,normalized=? WHERE chat_id=? AND id=?",
        (archived_key, scope, int(destination_id)),
    )
    return True

# --- Управление командой, инвентаризация и шаблоны отчётов step65 ---

def list_job_titles_detailed(chat_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        "SELECT * FROM job_titles WHERE chat_id=? AND is_archived=0 ORDER BY name",
        (scope,),
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["permissions"] = json.loads(item.get("permissions_json") or "{}")
        except Exception:
            item["permissions"] = {}
        result.append(item)
    return result


def _sync_job_access_flags(job_title_id: int) -> None:
    can_manage, can_view, can_submit = _access_flags_for_job(job_title_id)
    db.execute(
        """
        UPDATE account_user_access
        SET can_manage=?,can_view=?,can_submit=?,updated_at=CURRENT_TIMESTAMP
        WHERE job_title_id=?
        """,
        (can_manage, can_view, can_submit, int(job_title_id)),
    )


def update_job_title_record(chat_id: int, job_title_id: int, name: str, permissions: dict[str, bool]) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Укажите название должности."
    row = db.fetchone(
        "SELECT id FROM job_titles WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(job_title_id)),
    )
    if not row:
        return False, "Должность не найдена."
    conflict = db.fetchone(
        "SELECT id FROM job_titles WHERE chat_id=? AND normalized=? AND id<>? AND is_archived=0",
        (scope, key, int(job_title_id)),
    )
    if conflict:
        return False, "Такая должность уже есть."
    cleaned = {key_name: bool(permissions.get(key_name)) for key_name in PERMISSION_KEYS}
    db.execute(
        "UPDATE job_titles SET name=?,normalized=?,permissions_json=? WHERE chat_id=? AND id=?",
        (name.strip(), key, json.dumps(cleaned, ensure_ascii=False), scope, int(job_title_id)),
    )
    _sync_job_access_flags(int(job_title_id))
    return True, f"Должность обновлена: {name.strip()}"


def archive_job_title_record(chat_id: int, job_title_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    assigned = db.fetchone(
        "SELECT COUNT(*) AS count FROM workers WHERE chat_id=? AND job_title_id=? AND is_active=1",
        (scope, int(job_title_id)),
    )
    if assigned and int(assigned["count"] or 0) > 0:
        return False, "Сначала назначьте сотрудникам другую должность."
    row = db.fetchone(
        "SELECT id FROM job_titles WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(job_title_id)),
    )
    if not row:
        return False, "Должность не найдена."
    db.execute(
        "UPDATE job_titles SET is_archived=1 WHERE chat_id=? AND id=?",
        (scope, int(job_title_id)),
    )
    db.execute(
        "DELETE FROM area_section_access WHERE chat_id=? AND job_title_id=?",
        (scope, int(job_title_id)),
    )
    return True, "Должность удалена."


def list_workers_detailed(chat_id: int, include_inactive: bool = False) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    active_where = "" if include_inactive else "AND w.is_active=1"
    rows = db.fetchall(
        f"""
        SELECT w.user_id,w.display_name,w.job_title_id,j.name AS job_name,
               w.is_active,w.created_at,
               COALESCE(aua.can_manage,0) AS can_manage,
               COALESCE(aua.can_view,0) AS can_view,
               COALESCE(aua.can_submit,0) AS can_submit
        FROM workers w
        LEFT JOIN job_titles j ON j.id=w.job_title_id
        LEFT JOIN accounting_accounts aa ON aa.scope_chat_id=w.chat_id AND aa.is_archived=0
        LEFT JOIN account_user_access aua ON aua.account_id=aa.id AND aua.user_id=w.user_id
        WHERE w.chat_id=? {active_where}
        ORDER BY w.is_active DESC,w.display_name,w.user_id
        """,
        (scope,),
    )
    return [dict(row) for row in rows]


def save_worker_record(chat_id: int, user_id: int, display_name: str, job_title_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    job = db.fetchone(
        "SELECT id,name FROM job_titles WHERE chat_id=? AND id=? AND is_archived=0",
        (scope, int(job_title_id)),
    )
    if not job:
        return False, "Должность не найдена."
    if int(user_id) <= 0:
        return False, "Укажите Telegram ID сотрудника."
    set_worker_job(scope, int(user_id), display_name.strip() or str(user_id), int(job_title_id))
    return True, f"Сотрудник сохранён: {display_name.strip() or user_id}"


def archive_worker_record(chat_id: int, user_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT user_id FROM workers WHERE chat_id=? AND user_id=? AND is_active=1",
        (scope, int(user_id)),
    )
    if not row:
        return False, "Сотрудник не найден."
    db.execute(
        "UPDATE workers SET is_active=0 WHERE chat_id=? AND user_id=?",
        (scope, int(user_id)),
    )
    account = get_account_by_scope(scope)
    if account:
        db.execute(
            """
            UPDATE account_user_access
            SET can_manage=0,can_view=0,can_submit=0,updated_at=CURRENT_TIMESTAMP
            WHERE account_id=? AND user_id=?
            """,
            (account.id, int(user_id)),
        )
    return True, "Доступ сотрудника отключён."


def list_inventory_positions(chat_id: int, area_ids: set[int] | None = None) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["i.chat_id=?", "e.is_archived=0"]
    params: list[object] = [scope]
    if area_ids is not None:
        if not area_ids:
            return []
        marks = ",".join("?" for _ in area_ids)
        where.append(f"i.area_id IN ({marks})")
        params.extend(sorted(int(value) for value in area_ids))
    rows = db.fetchall(
        f"""
        SELECT i.area_id,a.name AS area_name,i.entity_type,i.entity_id,e.name AS entity_name,
               i.unit,i.quantity
        FROM inventory i
        JOIN entities e ON e.id=i.entity_id AND e.chat_id=i.chat_id
        LEFT JOIN areas a ON a.id=i.area_id
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(a.name,''),e.entity_type,e.name,i.unit
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def list_inventory_history(
    chat_id: int,
    *,
    area_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    limit: int = 60,
) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["o.chat_id=?"]
    params: list[object] = [scope]
    if area_id is not None:
        where.append("(o.area_id=? OR o.from_area_id=? OR o.to_area_id=?)")
        params.extend([int(area_id), int(area_id), int(area_id)])
    if entity_type:
        where.append("o.entity_type=?")
        params.append(str(entity_type))
    if entity_id is not None:
        where.append("o.entity_id=?")
        params.append(int(entity_id))
    params.append(max(1, min(int(limit), 200)))
    rows = db.fetchall(
        f"""
        SELECT o.id,o.created_at,o.user_id,o.operation_type,o.entity_type,o.entity_id,
               e.name AS entity_name,o.quantity,o.unit,o.raw_text,o.area_id,a.name AS area_name,
               o.from_area_id,af.name AS from_area_name,o.to_area_id,at.name AS to_area_name,
               CASE WHEN oc.id IS NULL THEN 0 ELSE 1 END AS is_corrected,
               w.display_name AS worker_name
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        LEFT JOIN areas af ON af.id=o.from_area_id
        LEFT JOIN areas at ON at.id=o.to_area_id
        LEFT JOIN operation_corrections oc ON oc.original_operation_id=o.id
        LEFT JOIN workers w ON w.chat_id=o.chat_id AND w.user_id=o.user_id
        WHERE {' AND '.join(where)}
        ORDER BY o.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def save_report_preset(
    chat_id: int,
    user_id: int,
    name: str,
    request_text: str,
    report_format: str = "xlsx",
    area_id: int | None = None,
    preset_id: int | None = None,
) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Укажите название шаблона.", None
    fmt = (report_format or "xlsx").lower()
    if fmt not in {"xlsx", "pdf"}:
        return False, "Неизвестный формат отчёта.", None
    if area_id is not None:
        area = db.fetchone(
            "SELECT id FROM areas WHERE chat_id=? AND id=? AND is_archived=0",
            (scope, int(area_id)),
        )
        if not area:
            return False, "Площадка не найдена.", None
    if preset_id:
        row = db.fetchone(
            "SELECT id FROM report_presets WHERE chat_id=? AND user_id=? AND id=? AND is_archived=0",
            (scope, int(user_id), int(preset_id)),
        )
        if not row:
            return False, "Шаблон не найден.", None
        conflict = db.fetchone(
            "SELECT id FROM report_presets WHERE chat_id=? AND user_id=? AND normalized=? AND id<>? AND is_archived=0",
            (scope, int(user_id), key, int(preset_id)),
        )
        if conflict:
            return False, "Такой шаблон уже есть.", None
        db.execute(
            """
            UPDATE report_presets
            SET name=?,normalized=?,request_text=?,report_format=?,area_id=?,updated_at=CURRENT_TIMESTAMP
            WHERE chat_id=? AND user_id=? AND id=?
            """,
            (name.strip(), key, request_text.strip() or "отчёт за месяц", fmt, area_id, scope, int(user_id), int(preset_id)),
        )
        return True, "Шаблон обновлён.", int(preset_id)
    existing = db.fetchone(
        "SELECT id,is_archived FROM report_presets WHERE chat_id=? AND user_id=? AND normalized=?",
        (scope, int(user_id), key),
    )
    if existing and not int(existing["is_archived"] or 0):
        return False, "Такой шаблон уже есть.", None
    if existing:
        saved_id = int(existing["id"])
        db.execute(
            """
            UPDATE report_presets
            SET name=?,request_text=?,report_format=?,area_id=?,is_archived=0,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (name.strip(), request_text.strip() or "отчёт за месяц", fmt, area_id, saved_id),
        )
        return True, "Шаблон сохранён.", saved_id
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO report_presets(chat_id,user_id,name,normalized,request_text,report_format,area_id)
            VALUES(?,?,?,?,?,?,?)
            """,
            (scope, int(user_id), name.strip(), key, request_text.strip() or "отчёт за месяц", fmt, area_id),
        )
        conn.commit()
        saved_id = int(cur.lastrowid)
    return True, "Шаблон сохранён.", saved_id


def list_report_presets(chat_id: int, user_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT rp.id,rp.name,rp.request_text,rp.report_format,rp.area_id,a.name AS area_name,
               rp.created_at,rp.updated_at
        FROM report_presets rp
        LEFT JOIN areas a ON a.id=rp.area_id
        WHERE rp.chat_id=? AND rp.user_id=? AND rp.is_archived=0
        ORDER BY rp.name
        """,
        (scope, int(user_id)),
    )
    return [dict(row) for row in rows]


def archive_report_preset(chat_id: int, user_id: int, preset_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id FROM report_presets WHERE chat_id=? AND user_id=? AND id=? AND is_archived=0",
        (scope, int(user_id), int(preset_id)),
    )
    if not row:
        return False
    db.execute("DELETE FROM report_schedules WHERE preset_id=?", (int(preset_id),))
    db.execute(
        "UPDATE report_presets SET is_archived=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(preset_id),),
    )
    return True

# Повторное создание архивированной должности восстанавливает её без новой строки.
def create_job_title(chat_id: int, name: str, permissions: dict[str, bool] | None = None) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    key = normalize_key(name)
    if not key:
        return False, "Название не найдено."
    cleaned = {permission: bool((permissions or {}).get(permission)) for permission in PERMISSION_KEYS}
    existing = db.fetchone(
        "SELECT id,is_archived FROM job_titles WHERE chat_id=? AND normalized=?",
        (scope, key),
    )
    if existing and not int(existing["is_archived"] or 0):
        return False, "Такая должность уже есть."
    payload = json.dumps(cleaned, ensure_ascii=False)
    if existing:
        db.execute(
            "UPDATE job_titles SET name=?,permissions_json=?,is_archived=0 WHERE chat_id=? AND id=?",
            (name.strip(), payload, scope, int(existing["id"])),
        )
        return True, f"Должность создана: {name.strip()}"
    db.execute(
        "INSERT INTO job_titles(chat_id,name,normalized,permissions_json) VALUES(?,?,?,?)",
        (scope, name.strip(), key, payload),
    )
    return True, f"Должность создана: {name.strip()}"


# --- Массовая инвентаризация, смены и расписания отчётов step66 ---

INVENTORY_SESSION_STATUSES = {"draft", "submitted", "approved", "rejected", "cancelled"}
REPORT_SCHEDULE_FREQUENCIES = {"daily", "weekly", "monthly"}


def create_inventory_session(chat_id: int, area_id: int, created_by: int, note: str = "") -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    area = get_area(int(area_id))
    if not area or area.chat_id != scope:
        return False, "Площадка не найдена.", None
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO inventory_sessions(chat_id,area_id,created_by,note) VALUES(?,?,?,?)",
            (scope, int(area_id), int(created_by), (note or "").strip()[:500]),
        )
        conn.commit()
        return True, "Инвентаризация создана.", int(cur.lastrowid)


def get_inventory_session(chat_id: int, session_id: int) -> dict | None:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT s.*,a.name AS area_name,
               COALESCE(NULLIF(w.display_name,''),CAST(s.created_by AS TEXT)) AS creator_name,
               COALESCE(NULLIF(dw.display_name,''),CAST(s.decided_by AS TEXT)) AS decider_name
        FROM inventory_sessions s
        JOIN areas a ON a.id=s.area_id
        LEFT JOIN workers w ON w.chat_id=s.chat_id AND w.user_id=s.created_by
        LEFT JOIN workers dw ON dw.chat_id=s.chat_id AND dw.user_id=s.decided_by
        WHERE s.chat_id=? AND s.id=?
        """,
        (scope, int(session_id)),
    )
    if not row:
        return None
    result = dict(row)
    result["items"] = list_inventory_session_items(scope, int(session_id))
    return result


def list_inventory_session_items(chat_id: int, session_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT i.*,e.name AS entity_name,e.default_unit
        FROM inventory_session_items i
        JOIN inventory_sessions s ON s.id=i.session_id AND s.chat_id=?
        JOIN entities e ON e.id=i.entity_id
        WHERE i.session_id=?
        ORDER BY e.name,i.id
        """,
        (scope, int(session_id)),
    )
    return [dict(row) for row in rows]


def list_inventory_sessions(chat_id: int, area_ids: set[int] | None = None, limit: int = 60) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["s.chat_id=?"]
    params: list[object] = [scope]
    if area_ids is not None:
        if not area_ids:
            return []
        placeholders = ",".join("?" for _ in area_ids)
        where.append(f"s.area_id IN ({placeholders})")
        params.extend(sorted(int(value) for value in area_ids))
    params.append(max(1, min(int(limit), 200)))
    rows = db.fetchall(
        f"""
        SELECT s.*,a.name AS area_name,
               COALESCE(NULLIF(w.display_name,''),CAST(s.created_by AS TEXT)) AS creator_name,
               COALESCE(NULLIF(dw.display_name,''),CAST(s.decided_by AS TEXT)) AS decider_name,
               COUNT(i.id) AS item_count,
               COALESCE(SUM(ABS(i.actual_quantity-i.system_quantity)),0) AS counted_difference
        FROM inventory_sessions s
        JOIN areas a ON a.id=s.area_id
        LEFT JOIN workers w ON w.chat_id=s.chat_id AND w.user_id=s.created_by
        LEFT JOIN workers dw ON dw.chat_id=s.chat_id AND dw.user_id=s.decided_by
        LEFT JOIN inventory_session_items i ON i.session_id=s.id
        WHERE {' AND '.join(where)}
        GROUP BY s.id
        ORDER BY CASE s.status WHEN 'submitted' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,s.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def save_inventory_session_item(
    chat_id: int,
    session_id: int,
    entity_type: str,
    entity_id: int,
    unit: str,
    actual_quantity: float,
    note: str = "",
) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    session = db.fetchone("SELECT * FROM inventory_sessions WHERE chat_id=? AND id=?", (scope, int(session_id)))
    if not session:
        return False, "Инвентаризация не найдена."
    if session["status"] != "draft":
        return False, "Изменять можно только черновик."
    entity = get_entity(int(entity_id))
    if not entity or entity.chat_id != scope or entity.entity_type != entity_type:
        return False, "Позиция не найдена."
    if entity_type not in {"component", "product", "material", "stock_item"}:
        return False, "Этот тип позиции нельзя пересчитывать."
    if float(actual_quantity) < 0:
        return False, "Количество не может быть меньше нуля."
    clean_unit = (unit or entity.default_unit or "шт").strip() or "шт"
    current = inventory_quantity(scope, entity_type, int(entity_id), clean_unit, int(session["area_id"]))
    db.execute(
        """
        INSERT INTO inventory_session_items(session_id,entity_type,entity_id,unit,system_quantity,actual_quantity,note)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(session_id,entity_type,entity_id,unit) DO UPDATE SET
          system_quantity=excluded.system_quantity,
          actual_quantity=excluded.actual_quantity,
          note=excluded.note,
          updated_at=CURRENT_TIMESTAMP
        """,
        (int(session_id), entity_type, int(entity_id), clean_unit, float(current), float(actual_quantity), (note or "").strip()[:500]),
    )
    return True, "Позиция добавлена в пересчёт."


def delete_inventory_session_item(chat_id: int, session_id: int, item_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """SELECT i.id FROM inventory_session_items i
           JOIN inventory_sessions s ON s.id=i.session_id
           WHERE s.chat_id=? AND s.id=? AND s.status='draft' AND i.id=?""",
        (scope, int(session_id), int(item_id)),
    )
    if not row:
        return False
    db.execute("DELETE FROM inventory_session_items WHERE id=?", (int(item_id),))
    return True


def submit_inventory_session(chat_id: int, session_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id,status,(SELECT COUNT(*) FROM inventory_session_items WHERE session_id=inventory_sessions.id) AS item_count FROM inventory_sessions WHERE chat_id=? AND id=?",
        (scope, int(session_id)),
    )
    if not row:
        return False, "Инвентаризация не найдена."
    if row["status"] != "draft":
        return False, "Этот пересчёт уже отправлен."
    if int(row["item_count"] or 0) <= 0:
        return False, "Добавьте хотя бы одну позицию."
    db.execute(
        "UPDATE inventory_sessions SET status='submitted',submitted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(session_id),),
    )
    return True, "Инвентаризация отправлена на подтверждение."


def decide_inventory_session(chat_id: int, session_id: int, actor_user_id: int, status: str, note: str = "") -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    if status not in {"approved", "rejected", "cancelled"}:
        return False, "Решение не поддерживается."
    row = db.fetchone("SELECT status FROM inventory_sessions WHERE chat_id=? AND id=?", (scope, int(session_id)))
    if not row:
        return False, "Инвентаризация не найдена."
    allowed_from = {"approved": "submitted", "rejected": "submitted", "cancelled": "draft"}
    if row["status"] != allowed_from[status]:
        return False, "Статус инвентаризации уже изменён."
    db.execute(
        """
        UPDATE inventory_sessions
        SET status=?,decided_by=?,decided_at=CURRENT_TIMESTAMP,decision_note=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (status, int(actor_user_id), (note or "").strip()[:500], int(session_id)),
    )
    labels = {"approved": "Инвентаризация подтверждена.", "rejected": "Инвентаризация отклонена.", "cancelled": "Черновик отменён."}
    return True, labels[status]


def record_inventory_session_application(session_id: int, item_id: int, approved_system_quantity: float, applied_delta: float) -> None:
    db.execute(
        "UPDATE inventory_session_items SET approved_system_quantity=?,applied_delta=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=? AND id=?",
        (float(approved_system_quantity), float(applied_delta), int(session_id), int(item_id)),
    )


def start_worker_shift(chat_id: int, user_id: int, area_id: int | None, started_by: int, note: str = "") -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    if area_id is not None:
        area = get_area(int(area_id))
        if not area or area.chat_id != scope:
            return False, "Площадка не найдена.", None
    existing = db.fetchone("SELECT id FROM worker_shifts WHERE chat_id=? AND user_id=? AND status='open'", (scope, int(user_id)))
    if existing:
        return False, "Смена уже начата.", int(existing["id"])
    started_at = datetime.now()
    plan = _match_shift_plan(scope, int(user_id), started_at)
    plan_id = int(plan["id"]) if plan else None
    start_deviation = None
    if plan:
        planned = datetime.fromisoformat(str(plan["planned_start"]))
        start_deviation = round((started_at - planned).total_seconds() / 60, 1)
        if area_id is None and plan.get("area_id") is not None:
            area_id = int(plan["area_id"])
    try:
        with db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO worker_shifts(chat_id,user_id,area_id,started_by,started_at,note,plan_id,start_deviation_minutes)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (scope, int(user_id), int(area_id) if area_id is not None else None, int(started_by), started_at.strftime("%Y-%m-%d %H:%M:%S"), (note or "").strip()[:500], plan_id, start_deviation),
            )
            if plan_id:
                conn.execute("UPDATE shift_plans SET status='in_progress',updated_at=CURRENT_TIMESTAMP WHERE id=?", (plan_id,))
            conn.commit()
            suffix = ""
            if start_deviation is not None:
                suffix = f" Отклонение от плана: {abs(start_deviation):.0f} мин. {'позже' if start_deviation > 0 else 'раньше'}." if abs(start_deviation) >= 0.5 else " Начало по плану."
            return True, "Смена начата." + suffix, int(cur.lastrowid)
    except Exception:
        return False, "Смена уже начата.", None


def end_worker_shift(chat_id: int, user_id: int, ended_by: int, note: str = "") -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT id,note,plan_id FROM worker_shifts WHERE chat_id=? AND user_id=? AND status='open'", (scope, int(user_id)))
    if not row:
        return False, "Открытая смена не найдена."
    merged_note = (str(row["note"] or "") + (" · " if row["note"] and note else "") + (note or "").strip())[:500]
    ended_at = datetime.now()
    end_deviation = None
    if row["plan_id"]:
        plan = db.fetchone("SELECT planned_end FROM shift_plans WHERE id=?", (int(row["plan_id"]),))
        if plan:
            planned_end = datetime.fromisoformat(str(plan["planned_end"]))
            end_deviation = round((ended_at - planned_end).total_seconds() / 60, 1)
    with db.connect() as conn:
        conn.execute(
            """UPDATE worker_shifts SET status='closed',ended_by=?,ended_at=?,note=?,end_deviation_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (int(ended_by), ended_at.strftime("%Y-%m-%d %H:%M:%S"), merged_note, end_deviation, int(row["id"])),
        )
        if row["plan_id"]:
            conn.execute("UPDATE shift_plans SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(row["plan_id"]),))
        conn.commit()
    suffix = ""
    if end_deviation is not None:
        suffix = f" Окончание: {abs(end_deviation):.0f} мин. {'позже плана' if end_deviation > 0 else 'раньше плана'}." if abs(end_deviation) >= 0.5 else " Окончание по плану."
    return True, "Смена завершена." + suffix


def list_worker_shifts(chat_id: int, user_id: int | None = None, limit: int = 80) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["s.chat_id=?"]
    params: list[object] = [scope]
    if user_id is not None:
        where.append("s.user_id=?")
        params.append(int(user_id))
    params.append(max(1, min(int(limit), 200)))
    rows = db.fetchall(
        f"""
        SELECT s.*,a.name AS area_name,
               COALESCE(NULLIF(w.display_name,''),CAST(s.user_id AS TEXT)) AS worker_name,
               ROUND((julianday(COALESCE(s.ended_at,CURRENT_TIMESTAMP))-julianday(s.started_at))*1440,1) AS duration_minutes
        FROM worker_shifts s
        LEFT JOIN areas a ON a.id=s.area_id
        LEFT JOIN workers w ON w.chat_id=s.chat_id AND w.user_id=s.user_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE s.status WHEN 'open' THEN 0 ELSE 1 END,s.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def worker_activity_analytics(chat_id: int, days: int = 30, user_id: int | None = None) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    days = max(1, min(int(days), 366))
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    user_filter = "AND people.user_id=?" if user_id is not None else ""
    params: list[object] = [scope, scope, start, scope, start, scope, start]
    if user_id is not None:
        params.append(int(user_id))
    rows = db.fetchall(
        f"""
        WITH people AS (
            SELECT user_id,MAX(display_name) AS display_name,MAX(job_title_id) AS job_title_id
            FROM workers WHERE chat_id=? GROUP BY user_id
            UNION
            SELECT user_id,'' AS display_name,NULL AS job_title_id FROM operations WHERE chat_id=? AND created_at>=? GROUP BY user_id
        ), op AS (
            SELECT user_id,COUNT(*) AS operation_count,COUNT(DISTINCT date(created_at)) AS active_days,
                   COALESCE(SUM(ABS(quantity)),0) AS total_quantity,MIN(created_at) AS first_activity,MAX(created_at) AS last_activity
            FROM operations WHERE chat_id=? AND created_at>=? GROUP BY user_id
        ), sh AS (
            SELECT user_id,COUNT(*) AS shift_count,
                   ROUND(SUM((julianday(COALESCE(ended_at,CURRENT_TIMESTAMP))-julianday(started_at))*1440),1) AS shift_minutes,
                   MAX(CASE WHEN status='open' THEN 1 ELSE 0 END) AS has_open_shift
            FROM worker_shifts WHERE chat_id=? AND started_at>=? GROUP BY user_id
        )
        SELECT people.user_id,
               COALESCE(NULLIF(people.display_name,''),CAST(people.user_id AS TEXT)) AS display_name,
               j.name AS job_name,
               COALESCE(op.operation_count,0) AS operation_count,
               COALESCE(op.active_days,0) AS active_days,
               COALESCE(op.total_quantity,0) AS total_quantity,
               op.first_activity,op.last_activity,
               COALESCE(sh.shift_count,0) AS shift_count,
               COALESCE(sh.shift_minutes,0) AS shift_minutes,
               COALESCE(sh.has_open_shift,0) AS has_open_shift
        FROM people
        LEFT JOIN job_titles j ON j.id=people.job_title_id
        LEFT JOIN op ON op.user_id=people.user_id
        LEFT JOIN sh ON sh.user_id=people.user_id
        WHERE 1=1 {user_filter}
        ORDER BY COALESCE(op.operation_count,0) DESC,display_name
        """,
        tuple(params),
    )
    result = []
    for row in rows:
        item = dict(row)
        shifts = int(item.get("shift_count") or 0)
        item["operations_per_shift"] = round(float(item.get("operation_count") or 0) / shifts, 2) if shifts else 0
        result.append(item)
    return result


def get_report_preset(chat_id: int, user_id: int, preset_id: int) -> dict | None:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """SELECT rp.*,a.name AS area_name FROM report_presets rp
           LEFT JOIN areas a ON a.id=rp.area_id
           WHERE rp.chat_id=? AND rp.user_id=? AND rp.id=? AND rp.is_archived=0""",
        (scope, int(user_id), int(preset_id)),
    )
    return dict(row) if row else None


def save_report_schedule(
    chat_id: int,
    user_id: int,
    preset_id: int,
    delivery_chat_id: int,
    frequency: str,
    hour: int,
    minute: int,
    weekday: int,
    month_day: int,
    next_run_at: str,
    enabled: bool = True,
    timezone_name: str = "server",
) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    preset = get_report_preset(scope, user_id, preset_id)
    if not preset:
        return False, "Шаблон отчёта не найден.", None
    if frequency not in REPORT_SCHEDULE_FREQUENCIES:
        return False, "Периодичность не поддерживается.", None
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59 and 0 <= int(weekday) <= 6 and 1 <= int(month_day) <= 28):
        return False, "Время расписания указано неверно.", None
    existing = db.fetchone("SELECT id FROM report_schedules WHERE preset_id=?", (int(preset_id),))
    if existing:
        db.execute(
            """
            UPDATE report_schedules SET delivery_chat_id=?,frequency=?,hour=?,minute=?,weekday=?,month_day=?,
                   is_enabled=?,next_run_at=?,timezone_name=?,last_error='',updated_at=CURRENT_TIMESTAMP
            WHERE preset_id=?
            """,
            (int(delivery_chat_id), frequency, int(hour), int(minute), int(weekday), int(month_day), 1 if enabled else 0, next_run_at, timezone_name[:80], int(preset_id)),
        )
        return True, "Расписание обновлено.", int(existing["id"])
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO report_schedules(preset_id,chat_id,user_id,delivery_chat_id,frequency,hour,minute,weekday,month_day,is_enabled,next_run_at,timezone_name)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (int(preset_id), scope, int(user_id), int(delivery_chat_id), frequency, int(hour), int(minute), int(weekday), int(month_day), 1 if enabled else 0, next_run_at, timezone_name[:80]),
        )
        conn.commit()
        return True, "Расписание создано.", int(cur.lastrowid)


def list_report_schedules(chat_id: int, user_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT s.*,rp.name AS preset_name,rp.request_text,rp.report_format,rp.area_id,a.name AS area_name
        FROM report_schedules s
        JOIN report_presets rp ON rp.id=s.preset_id AND rp.is_archived=0
        LEFT JOIN areas a ON a.id=rp.area_id
        WHERE s.chat_id=? AND s.user_id=?
        ORDER BY s.is_enabled DESC,s.next_run_at,s.id
        """,
        (scope, int(user_id)),
    )
    return [dict(row) for row in rows]


def delete_report_schedule(chat_id: int, user_id: int, schedule_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT id FROM report_schedules WHERE chat_id=? AND user_id=? AND id=?", (scope, int(user_id), int(schedule_id)))
    if not row:
        return False
    db.execute("DELETE FROM report_schedules WHERE id=?", (int(schedule_id),))
    return True


def list_due_report_schedules(now_text: str, limit: int = 20) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT s.*,rp.name AS preset_name,rp.request_text,rp.report_format,rp.area_id
        FROM report_schedules s
        JOIN report_presets rp ON rp.id=s.preset_id AND rp.is_archived=0
        WHERE s.is_enabled=1 AND s.next_run_at<=?
        ORDER BY s.next_run_at,s.id
        LIMIT ?
        """,
        (now_text, max(1, min(int(limit), 100))),
    )
    return [dict(row) for row in rows]


def mark_report_schedule_running(schedule_id: int, next_run_at: str) -> None:
    db.execute(
        "UPDATE report_schedules SET next_run_at=?,last_status='running',last_error='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (next_run_at, int(schedule_id)),
    )


def mark_report_schedule_result(schedule_id: int, success: bool, error: str = "") -> None:
    db.execute(
        """UPDATE report_schedules SET last_run_at=CURRENT_TIMESTAMP,last_status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        ("sent" if success else "error", (error or "")[:500], int(schedule_id)),
    )


# --- Входящие, планирование смен и история доставки step67 ---

INBOX_STATUSES = {"unread", "read", "resolved"}
SHIFT_PLAN_STATUSES = {"planned", "in_progress", "completed", "cancelled"}
REPORT_DELIVERY_STATUSES = {"queued", "running", "sent", "error"}


def _approval_recipient_ids(chat_id: int, area_id: int | None = None) -> list[int]:
    scope = resolve_scope_chat_id(chat_id)
    recipients: set[int] = set(tenant_admin_user_ids(scope))
    account = get_account_by_scope(scope)
    if account:
        recipients.add(int(account.owner_user_id))
        rows = db.fetchall(
            "SELECT user_id FROM account_user_access WHERE account_id=? AND can_manage=1 AND can_view=1",
            (account.id,),
        )
        recipients.update(int(row["user_id"]) for row in rows)
    rows = db.fetchall(
        """
        SELECT w.user_id,j.permissions_json
        FROM workers w
        JOIN job_titles j ON j.id=w.job_title_id AND j.is_archived=0
        WHERE w.chat_id=? AND w.is_active=1
        """,
        (scope,),
    )
    for row in rows:
        try:
            permissions = json.loads(row["permissions_json"] or "{}")
        except Exception:
            permissions = {}
        if permissions.get("stock") and permissions.get("edit"):
            recipients.add(int(row["user_id"]))
    result = []
    for user_id in sorted(recipients):
        if area_id is not None and not is_tenant_admin(scope, user_id):
            if not user_area_action_allowed(scope, user_id, "inventory", int(area_id), "edit"):
                continue
        result.append(user_id)
    return result


def create_inbox_item(
    chat_id: int,
    recipient_user_id: int,
    kind: str,
    title: str,
    message: str = "",
    related_type: str = "",
    related_id: int | None = None,
    *,
    deduplicate: bool = True,
    priority: str = "normal",
    force: bool = False,
) -> int:
    scope = resolve_scope_chat_id(chat_id)
    preferences = get_notification_preferences(scope, int(recipient_user_id))
    if not force and not notification_kind_enabled(kind, preferences):
        return 0
    site_visible = 1 if preferences.get("inbox_enabled", True) else 0
    telegram_status = "queued" if preferences.get("telegram_enabled", True) else "disabled"
    if not site_visible and telegram_status == "disabled":
        return 0
    if deduplicate:
        row = db.fetchone(
            """
            SELECT id FROM inbox_items
            WHERE chat_id=? AND recipient_user_id=? AND kind=? AND related_type=?
              AND COALESCE(related_id,-1)=COALESCE(?,-1) AND status IN ('unread','read')
            ORDER BY id DESC LIMIT 1
            """,
            (scope, int(recipient_user_id), kind[:80], related_type[:80], related_id),
        )
        if row:
            return int(row["id"])
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO inbox_items(
                chat_id,recipient_user_id,kind,title,message,related_type,related_id,
                telegram_status,priority,site_visible
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scope, int(recipient_user_id), kind[:80], title[:180], message[:1000],
                related_type[:80], related_id, telegram_status,
                priority if priority in {"normal", "high", "urgent"} else "normal", site_visible,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def queue_inventory_approval_notifications(chat_id: int, session_id: int) -> int:
    scope = resolve_scope_chat_id(chat_id)
    session = get_inventory_session(scope, session_id)
    if not session:
        return 0
    created = 0
    for user_id in _approval_recipient_ids(scope, int(session["area_id"])):
        if int(user_id) == int(session.get("created_by") or 0) and not is_tenant_admin(scope, user_id):
            continue
        create_inbox_item(
            scope,
            user_id,
            "inventory_approval",
            f"Инвентаризация №{session_id} ожидает решения",
            f"Площадка: {session.get('area_name') or 'не указана'}. Автор: {session.get('creator_name') or session.get('created_by')}",
            "inventory_session",
            int(session_id),
        )
        created += 1
    return created


def resolve_inventory_approval_notifications(chat_id: int, session_id: int, status: str, actor_user_id: int) -> None:
    scope = resolve_scope_chat_id(chat_id)
    db.execute(
        """
        UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP
        WHERE chat_id=? AND kind IN ('inventory_approval','inventory_approval_reminder') AND related_type='inventory_session'
          AND related_id=? AND status!='resolved'
        """,
        (scope, int(session_id)),
    )
    session = get_inventory_session(scope, session_id)
    if not session:
        return
    author = int(session.get("created_by") or 0)
    label = {"approved": "подтверждена", "rejected": "отклонена", "cancelled": "отменена"}.get(status, status)
    create_inbox_item(
        scope,
        author,
        "inventory_result",
        f"Инвентаризация №{session_id} {label}",
        f"Площадка: {session.get('area_name') or 'не указана'}. Ответственный: {session.get('decider_name') or actor_user_id}",
        "inventory_session",
        int(session_id),
        deduplicate=False,
    )


def list_inbox_items(chat_id: int, recipient_user_id: int, limit: int = 100, include_resolved: bool = True) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = "" if include_resolved else "AND i.status!='resolved'"
    rows = db.fetchall(
        f"""
        SELECT i.*,s.status AS related_status,a.name AS area_name
        FROM inbox_items i
        LEFT JOIN inventory_sessions s ON i.related_type='inventory_session' AND s.id=i.related_id AND s.chat_id=i.chat_id
        LEFT JOIN areas a ON a.id=s.area_id
        WHERE i.chat_id=? AND i.recipient_user_id=? AND i.site_visible=1 {where}
        ORDER BY CASE i.status WHEN 'unread' THEN 0 WHEN 'read' THEN 1 ELSE 2 END,i.id DESC
        LIMIT ?
        """,
        (scope, int(recipient_user_id), max(1, min(int(limit), 300))),
    )
    return [dict(row) for row in rows]


def mark_inbox_item_read(chat_id: int, recipient_user_id: int, item_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id,status FROM inbox_items WHERE chat_id=? AND recipient_user_id=? AND id=?",
        (scope, int(recipient_user_id), int(item_id)),
    )
    if not row:
        return False
    if row["status"] == "unread":
        db.execute("UPDATE inbox_items SET status='read',read_at=CURRENT_TIMESTAMP WHERE id=?", (int(item_id),))
    return True


def list_pending_inbox_telegram(limit: int = 30) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT * FROM inbox_items
        WHERE telegram_status='queued'
           OR (telegram_status='error' AND (telegram_next_attempt_at IS NULL OR telegram_next_attempt_at<=CURRENT_TIMESTAMP))
        ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                 CASE telegram_status WHEN 'queued' THEN 0 ELSE 1 END,id
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    )
    return [dict(row) for row in rows]


def mark_inbox_telegram_result(item_id: int, success: bool, error: str = "") -> None:
    row = db.fetchone("SELECT telegram_attempts FROM inbox_items WHERE id=?", (int(item_id),))
    attempts = int(row["telegram_attempts"] or 0) + (0 if success else 1) if row else (0 if success else 1)
    if success:
        db.execute(
            """UPDATE inbox_items SET telegram_status='sent',telegram_error='',telegram_attempts=?,
                      telegram_next_attempt_at=NULL,sent_at=CURRENT_TIMESTAMP WHERE id=?""",
            (attempts, int(item_id)),
        )
        return
    # 2, 4, 8 ... минут, максимум 6 часов. Ошибка одного получателя не забивает очередь.
    delay_minutes = min(360, 2 ** min(max(1, attempts), 8))
    next_attempt = (datetime.now() + timedelta(minutes=delay_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """UPDATE inbox_items SET telegram_status='error',telegram_error=?,telegram_attempts=?,
                  telegram_next_attempt_at=? WHERE id=?""",
        ((error or "")[:500], attempts, next_attempt, int(item_id)),
    )


def create_shift_plan(
    chat_id: int,
    user_id: int,
    area_id: int | None,
    planned_start: str,
    planned_end: str,
    created_by: int,
    note: str = "",
    template_id: int | None = None,
    occurrence_date: str | None = None,
) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    try:
        start_dt = datetime.fromisoformat(planned_start.replace("T", " "))
        end_dt = datetime.fromisoformat(planned_end.replace("T", " "))
    except ValueError:
        return False, "Время смены указано неверно.", None
    if end_dt <= start_dt:
        return False, "Окончание смены должно быть позже начала.", None
    if (end_dt - start_dt).total_seconds() > 24 * 3600:
        return False, "Плановая смена не может быть длиннее суток.", None
    if area_id is not None:
        area = get_area(int(area_id))
        if not area or area.chat_id != scope:
            return False, "Площадка не найдена.", None
    if template_id is not None and occurrence_date:
        existing = db.fetchone(
            "SELECT id FROM shift_plans WHERE template_id=? AND occurrence_date=? LIMIT 1",
            (int(template_id), occurrence_date),
        )
        if existing:
            return True, "Плановая смена уже создана.", int(existing["id"])
    overlap = db.fetchone(
        """
        SELECT id FROM shift_plans
        WHERE chat_id=? AND user_id=? AND status IN ('planned','in_progress')
          AND planned_start<? AND planned_end>?
        LIMIT 1
        """,
        (scope, int(user_id), end_dt.strftime("%Y-%m-%d %H:%M:%S"), start_dt.strftime("%Y-%m-%d %H:%M:%S")),
    )
    if overlap:
        return False, "На это время уже есть плановая смена.", int(overlap["id"])
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO shift_plans(
                chat_id,user_id,area_id,planned_start,planned_end,created_by,note,template_id,occurrence_date
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                scope, int(user_id), int(area_id) if area_id is not None else None,
                start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                int(created_by), (note or "")[:500], int(template_id) if template_id is not None else None,
                occurrence_date,
            ),
        )
        conn.commit()
        return True, "Плановая смена создана.", int(cur.lastrowid)


def list_shift_plans(chat_id: int, user_id: int | None = None, limit: int = 120) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["p.chat_id=?"]
    params: list[object] = [scope]
    if user_id is not None:
        where.append("p.user_id=?")
        params.append(int(user_id))
    params.append(max(1, min(int(limit), 300)))
    rows = db.fetchall(
        f"""
        SELECT p.*,a.name AS area_name,
               COALESCE(NULLIF(w.display_name,''),CAST(p.user_id AS TEXT)) AS worker_name,
               s.id AS actual_shift_id,s.started_at AS actual_started_at,s.ended_at AS actual_ended_at,
               s.start_deviation_minutes,s.end_deviation_minutes
        FROM shift_plans p
        LEFT JOIN areas a ON a.id=p.area_id
        LEFT JOIN workers w ON w.chat_id=p.chat_id AND w.user_id=p.user_id
        LEFT JOIN worker_shifts s ON s.plan_id=p.id
        WHERE {' AND '.join(where)}
        ORDER BY CASE p.status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,p.planned_start DESC,p.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def cancel_shift_plan(chat_id: int, plan_id: int, actor_user_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT status FROM shift_plans WHERE chat_id=? AND id=?", (scope, int(plan_id)))
    if not row:
        return False, "Плановая смена не найдена."
    if row["status"] in {"in_progress", "completed"}:
        return False, "Начатую или завершённую смену отменить нельзя."
    db.execute(
        "UPDATE shift_plans SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(plan_id),),
    )
    return True, "Плановая смена отменена."


def _match_shift_plan(chat_id: int, user_id: int, started_at: datetime) -> dict | None:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT * FROM shift_plans
        WHERE chat_id=? AND user_id=? AND status='planned'
          AND planned_start>=? AND planned_start<=?
        ORDER BY ABS((julianday(planned_start)-julianday(?))*1440),id
        LIMIT 1
        """,
        (
            scope,
            int(user_id),
            (started_at - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S"),
            (started_at + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S"),
            started_at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return dict(rows[0]) if rows else None


def attendance_deviations(chat_id: int, user_id: int | None = None, days: int = 30, limit: int = 160) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    start = (datetime.now() - timedelta(days=max(1, min(int(days), 366)))).strftime("%Y-%m-%d %H:%M:%S")
    where = ["p.chat_id=?", "p.planned_start>=?"]
    params: list[object] = [scope, start]
    if user_id is not None:
        where.append("p.user_id=?")
        params.append(int(user_id))
    params.append(max(1, min(int(limit), 300)))
    rows = db.fetchall(
        f"""
        SELECT p.id AS plan_id,p.user_id,p.planned_start,p.planned_end,p.status AS plan_status,p.note,
               a.name AS area_name,COALESCE(NULLIF(w.display_name,''),CAST(p.user_id AS TEXT)) AS worker_name,
               s.id AS shift_id,s.started_at,s.ended_at,s.status AS shift_status,
               s.start_deviation_minutes,s.end_deviation_minutes
        FROM shift_plans p
        LEFT JOIN worker_shifts s ON s.plan_id=p.id
        LEFT JOIN areas a ON a.id=p.area_id
        LEFT JOIN workers w ON w.chat_id=p.chat_id AND w.user_id=p.user_id
        WHERE {' AND '.join(where)}
        ORDER BY p.planned_start DESC,p.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def create_report_delivery_history(schedule: dict, trigger_type: str = "scheduled", status: str = "running", retry_of: int | None = None) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO report_delivery_history(
                schedule_id,chat_id,user_id,preset_id,preset_name,trigger_type,status,
                delivery_chat_id,report_format,retry_of,started_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,CASE WHEN ?='running' THEN CURRENT_TIMESTAMP ELSE NULL END)
            """,
            (
                int(schedule["id"]), int(schedule["chat_id"]), int(schedule["user_id"]),
                int(schedule.get("preset_id") or 0) or None, str(schedule.get("preset_name") or ""),
                trigger_type, status, int(schedule["delivery_chat_id"]),
                str(schedule.get("report_format") or "xlsx"), retry_of, status,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_report_delivery_history(chat_id: int, user_id: int, limit: int = 120) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT h.*,s.timezone_name
        FROM report_delivery_history h
        LEFT JOIN report_schedules s ON s.id=h.schedule_id
        WHERE h.chat_id=? AND h.user_id=?
        ORDER BY h.id DESC LIMIT ?
        """,
        (scope, int(user_id), max(1, min(int(limit), 300))),
    )
    return [dict(row) for row in rows]


def queue_report_delivery_retry(chat_id: int, user_id: int, schedule_id: int, retry_of: int | None = None) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT s.*,rp.name AS preset_name,rp.request_text,rp.report_format,rp.area_id
        FROM report_schedules s JOIN report_presets rp ON rp.id=s.preset_id AND rp.is_archived=0
        WHERE s.chat_id=? AND s.user_id=? AND s.id=?
        """,
        (scope, int(user_id), int(schedule_id)),
    )
    if not row:
        return False, "Расписание не найдено.", None
    schedule = dict(row)
    pending = db.fetchone(
        "SELECT id FROM report_delivery_history WHERE schedule_id=? AND status IN ('queued','running') LIMIT 1",
        (int(schedule_id),),
    )
    if pending:
        return False, "Повторная отправка уже ожидает выполнения.", int(pending["id"])
    history_id = create_report_delivery_history(schedule, "manual", "queued", retry_of)
    return True, "Повторная отправка поставлена в очередь.", history_id


def list_queued_report_deliveries(limit: int = 20) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT h.*,s.preset_id,s.frequency,s.hour,s.minute,s.weekday,s.month_day,s.is_enabled,
               s.next_run_at,s.timezone_name,rp.name AS preset_name,rp.request_text,rp.report_format,rp.area_id
        FROM report_delivery_history h
        JOIN report_schedules s ON s.id=h.schedule_id
        JOIN report_presets rp ON rp.id=s.preset_id AND rp.is_archived=0
        WHERE h.status='queued'
        ORDER BY h.id LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    )
    return [dict(row) for row in rows]


def mark_report_delivery_running(history_id: int) -> None:
    db.execute(
        "UPDATE report_delivery_history SET status='running',started_at=CURRENT_TIMESTAMP,error='' WHERE id=? AND status='queued'",
        (int(history_id),),
    )


def mark_report_delivery_result(history_id: int, success: bool, error: str = "") -> None:
    db.execute(
        """
        UPDATE report_delivery_history SET status=?,finished_at=CURRENT_TIMESTAMP,error=? WHERE id=?
        """,
        ("sent" if success else "error", (error or "")[:500], int(history_id)),
    )


# --- Повторяющиеся смены, настройки уведомлений и посещаемость step68 ---

NOTIFICATION_DEFAULTS = {
    "inbox_enabled": True,
    "telegram_enabled": True,
    "inventory_approval_enabled": True,
    "inventory_result_enabled": True,
    "shift_plan_enabled": True,
    "approval_reminders_enabled": True,
    "reminder_after_minutes": 60,
    "repeat_every_minutes": 120,
    "max_reminders": 3,
}


def get_notification_preferences(chat_id: int, user_id: int) -> dict:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT * FROM notification_preferences WHERE chat_id=? AND user_id=?",
        (scope, int(user_id)),
    )
    result = dict(NOTIFICATION_DEFAULTS)
    result.update({"chat_id": scope, "user_id": int(user_id)})
    if row:
        raw = dict(row)
        for key in (
            "inbox_enabled", "telegram_enabled", "inventory_approval_enabled",
            "inventory_result_enabled", "shift_plan_enabled", "approval_reminders_enabled",
        ):
            raw[key] = bool(raw.get(key))
        result.update(raw)
    return result


def notification_kind_enabled(kind: str, preferences: dict) -> bool:
    if kind in {"inventory_approval", "inventory_approval_reminder"}:
        return bool(preferences.get("inventory_approval_enabled", True))
    if kind == "inventory_result":
        return bool(preferences.get("inventory_result_enabled", True))
    if kind == "shift_plan":
        return bool(preferences.get("shift_plan_enabled", True))
    return True


def save_notification_preferences(chat_id: int, user_id: int, values: dict) -> dict:
    scope = resolve_scope_chat_id(chat_id)
    current = get_notification_preferences(scope, user_id)
    current.update(values or {})
    reminder_after = max(5, min(int(current.get("reminder_after_minutes") or 60), 10080))
    repeat_every = max(5, min(int(current.get("repeat_every_minutes") or 120), 10080))
    max_reminders = max(0, min(int(current.get("max_reminders") or 0), 10))
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO notification_preferences(
                chat_id,user_id,inbox_enabled,telegram_enabled,inventory_approval_enabled,
                inventory_result_enabled,shift_plan_enabled,approval_reminders_enabled,
                reminder_after_minutes,repeat_every_minutes,max_reminders,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
                inbox_enabled=excluded.inbox_enabled,telegram_enabled=excluded.telegram_enabled,
                inventory_approval_enabled=excluded.inventory_approval_enabled,
                inventory_result_enabled=excluded.inventory_result_enabled,
                shift_plan_enabled=excluded.shift_plan_enabled,
                approval_reminders_enabled=excluded.approval_reminders_enabled,
                reminder_after_minutes=excluded.reminder_after_minutes,
                repeat_every_minutes=excluded.repeat_every_minutes,max_reminders=excluded.max_reminders,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                scope, int(user_id), int(bool(current.get("inbox_enabled", True))),
                int(bool(current.get("telegram_enabled", True))),
                int(bool(current.get("inventory_approval_enabled", True))),
                int(bool(current.get("inventory_result_enabled", True))),
                int(bool(current.get("shift_plan_enabled", True))),
                int(bool(current.get("approval_reminders_enabled", True))),
                reminder_after, repeat_every, max_reminders,
            ),
        )
        conn.commit()
    return get_notification_preferences(scope, user_id)


def _parse_hhmm(value: str) -> time:
    return datetime.strptime((value or "").strip(), "%H:%M").time()


def save_shift_template(
    chat_id: int, user_id: int, area_id: int | None, pattern_type: str, weekdays: list[int],
    cycle_work_days: int, cycle_rest_days: int, cycle_anchor_date: str | None,
    start_time: str, end_time: str, valid_from: str, valid_until: str | None,
    created_by: int, note: str = "", template_id: int | None = None, enabled: bool = True,
) -> tuple[bool, str, int | None]:
    scope = resolve_scope_chat_id(chat_id)
    worker = db.fetchone("SELECT is_active FROM workers WHERE chat_id=? AND user_id=?", (scope, int(user_id)))
    account = get_account_by_scope(scope)
    if not worker and not (account and int(account.owner_user_id) == int(user_id)) and not is_tenant_admin(scope, user_id):
        return False, "Сотрудник не найден.", None
    if worker and not bool(worker["is_active"]):
        return False, "Сотрудник отключён.", None
    if area_id is not None:
        area = get_area(int(area_id))
        if not area or area.chat_id != scope:
            return False, "Площадка не найдена.", None
    pattern_type = (pattern_type or "weekly").strip().lower()
    if pattern_type not in {"weekly", "cycle"}:
        return False, "Тип графика не поддерживается.", None
    try:
        start_clock = _parse_hhmm(start_time)
        end_clock = _parse_hhmm(end_time)
        start_day = date.fromisoformat(valid_from)
        end_day = date.fromisoformat(valid_until) if valid_until else None
    except ValueError:
        return False, "Дата или время графика указаны неверно.", None
    if end_day and end_day < start_day:
        return False, "Дата окончания графика раньше даты начала.", None
    clean_weekdays = sorted({int(x) for x in weekdays if 0 <= int(x) <= 6})
    work_days = max(1, min(int(cycle_work_days or 1), 31))
    rest_days = max(1, min(int(cycle_rest_days or 1), 31))
    anchor = cycle_anchor_date or valid_from
    try:
        date.fromisoformat(anchor)
    except ValueError:
        return False, "Дата начала цикла указана неверно.", None
    if pattern_type == "weekly" and not clean_weekdays:
        return False, "Выберите хотя бы один день недели.", None
    base = datetime.combine(start_day, start_clock)
    end_base = datetime.combine(start_day, end_clock)
    if end_base <= base:
        end_base += timedelta(days=1)
    if end_base - base > timedelta(hours=24):
        return False, "Смена не может быть длиннее суток.", None
    payload = (
        scope, int(user_id), int(area_id) if area_id is not None else None, pattern_type,
        json.dumps(clean_weekdays, ensure_ascii=False), work_days, rest_days, anchor,
        start_clock.strftime("%H:%M"), end_clock.strftime("%H:%M"), str(start_day),
        str(end_day) if end_day else None, int(bool(enabled)), int(created_by), (note or "")[:500],
    )
    with db.connect() as conn:
        if template_id is not None:
            row = conn.execute("SELECT id FROM shift_templates WHERE chat_id=? AND id=?", (scope, int(template_id))).fetchone()
            if not row:
                return False, "Шаблон смен не найден.", None
            future_rows = conn.execute(
                "SELECT id FROM shift_plans WHERE template_id=? AND status='planned' AND planned_start>=CURRENT_TIMESTAMP",
                (int(template_id),),
            ).fetchall()
            future_ids = [int(item["id"]) for item in future_rows]
            if future_ids:
                marks = ",".join("?" for _ in future_ids)
                conn.execute(
                    f"UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE related_type='shift_plan' AND related_id IN ({marks}) AND status!='resolved'",
                    tuple(future_ids),
                )
                conn.execute(f"DELETE FROM shift_plans WHERE id IN ({marks})", tuple(future_ids))
            conn.execute(
                """UPDATE shift_templates SET user_id=?,area_id=?,pattern_type=?,weekdays_json=?,
                   cycle_work_days=?,cycle_rest_days=?,cycle_anchor_date=?,start_time=?,end_time=?,
                   valid_from=?,valid_until=?,is_enabled=?,created_by=?,note=?,last_generated_until=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND id=?""",
                payload[1:] + (scope, int(template_id)),
            )
            saved_id = int(template_id)
        else:
            cur = conn.execute(
                """INSERT INTO shift_templates(
                   chat_id,user_id,area_id,pattern_type,weekdays_json,cycle_work_days,cycle_rest_days,
                   cycle_anchor_date,start_time,end_time,valid_from,valid_until,is_enabled,created_by,note
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                payload,
            )
            saved_id = int(cur.lastrowid)
        conn.commit()
    generate_shift_plans_from_templates(datetime.now(), scope_chat_id=scope, template_id=saved_id)
    return True, "Повторяющийся график сохранён.", saved_id


def list_shift_templates(chat_id: int, user_id: int | None = None) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    where = ["t.chat_id=?"]
    params: list[object] = [scope]
    if user_id is not None:
        where.append("t.user_id=?")
        params.append(int(user_id))
    rows = db.fetchall(
        f"""SELECT t.*,a.name AS area_name,
            COALESCE(NULLIF(w.display_name,''),CAST(t.user_id AS TEXT)) AS worker_name
            FROM shift_templates t
            LEFT JOIN areas a ON a.id=t.area_id
            LEFT JOIN workers w ON w.chat_id=t.chat_id AND w.user_id=t.user_id
            WHERE {' AND '.join(where)} ORDER BY t.is_enabled DESC,t.id DESC""",
        tuple(params),
    )
    result=[]
    for row in rows:
        item=dict(row)
        try: item["weekdays"] = json.loads(item.get("weekdays_json") or "[]")
        except Exception: item["weekdays"] = []
        result.append(item)
    return result


def disable_shift_template(chat_id: int, template_id: int, actor_user_id: int) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT id FROM shift_templates WHERE chat_id=? AND id=?", (scope, int(template_id)))
    if not row:
        return False, "Шаблон смен не найден."
    with db.connect() as conn:
        future_rows = conn.execute(
            "SELECT id FROM shift_plans WHERE template_id=? AND status='planned' AND planned_start>=CURRENT_TIMESTAMP",
            (int(template_id),),
        ).fetchall()
        future_ids = [int(item["id"]) for item in future_rows]
        if future_ids:
            marks = ",".join("?" for _ in future_ids)
            conn.execute(
                f"UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE related_type='shift_plan' AND related_id IN ({marks}) AND status!='resolved'",
                tuple(future_ids),
            )
            conn.execute(f"DELETE FROM shift_plans WHERE id IN ({marks})", tuple(future_ids))
        conn.execute("UPDATE shift_templates SET is_enabled=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(template_id),))
        conn.commit()
    return True, "Повторяющийся график отключён."


def _template_matches_day(template: dict, current_day: date) -> bool:
    if template.get("pattern_type") == "cycle":
        anchor = date.fromisoformat(str(template.get("cycle_anchor_date") or template["valid_from"]))
        offset = (current_day - anchor).days
        if offset < 0:
            return False
        work_days = max(1, int(template.get("cycle_work_days") or 1))
        rest_days = max(1, int(template.get("cycle_rest_days") or 1))
        return offset % (work_days + rest_days) < work_days
    try:
        weekdays = {int(x) for x in json.loads(template.get("weekdays_json") or "[]")}
    except Exception:
        weekdays = set()
    return current_day.weekday() in weekdays


def generate_shift_plans_from_templates(
    now: datetime | None = None, horizon_days: int = 45, scope_chat_id: int | None = None, template_id: int | None = None
) -> int:
    now = now or datetime.now()
    where = ["is_enabled=1"]
    params: list[object] = []
    if scope_chat_id is not None:
        where.append("chat_id=?")
        params.append(resolve_scope_chat_id(scope_chat_id))
    if template_id is not None:
        where.append("id=?")
        params.append(int(template_id))
    templates = [dict(r) for r in db.fetchall(f"SELECT * FROM shift_templates WHERE {' AND '.join(where)}", tuple(params))]
    created = 0
    horizon = now.date() + timedelta(days=max(1, min(int(horizon_days), 120)))
    for template in templates:
        start_day = max(now.date(), date.fromisoformat(str(template["valid_from"])))
        if template.get("last_generated_until"):
            try: start_day = max(start_day, date.fromisoformat(str(template["last_generated_until"])) + timedelta(days=1))
            except ValueError: pass
        end_day = horizon
        if template.get("valid_until"):
            end_day = min(end_day, date.fromisoformat(str(template["valid_until"])))
        current = start_day
        while current <= end_day:
            if _template_matches_day(template, current):
                start_clock = _parse_hhmm(str(template["start_time"]))
                end_clock = _parse_hhmm(str(template["end_time"]))
                planned_start = datetime.combine(current, start_clock)
                planned_end = datetime.combine(current, end_clock)
                if planned_end <= planned_start:
                    planned_end += timedelta(days=1)
                ok, message, plan_id = create_shift_plan(
                    int(template["chat_id"]), int(template["user_id"]),
                    int(template["area_id"]) if template.get("area_id") is not None else None,
                    planned_start.strftime("%Y-%m-%d %H:%M:%S"),
                    planned_end.strftime("%Y-%m-%d %H:%M:%S"), int(template["created_by"]),
                    str(template.get("note") or ""), int(template["id"]), str(current),
                )
                if ok and plan_id and message != "Плановая смена уже создана.":
                    created += 1
                    create_inbox_item(
                        int(template["chat_id"]), int(template["user_id"]), "shift_plan",
                        "Назначена смена по графику",
                        f"Начало: {planned_start:%d.%m.%Y %H:%M}. Окончание: {planned_end:%d.%m.%Y %H:%M}.",
                        "shift_plan", int(plan_id), deduplicate=False,
                    )
            current += timedelta(days=1)
        if end_day >= start_day:
            db.execute("UPDATE shift_templates SET last_generated_until=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(end_day), int(template["id"])))
    return created


def shift_calendar(
    chat_id: int, start_date: str, end_date: str, user_id: int | None = None, area_id: int | None = None
) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return []
    if end < start or (end-start).days > 370:
        return []
    where=["p.chat_id=?", "p.planned_start>=?", "p.planned_start<?"]
    params: list[object]=[scope, str(start), str(end+timedelta(days=1))]
    if user_id is not None:
        where.append("p.user_id=?"); params.append(int(user_id))
    if area_id is not None:
        where.append("p.area_id=?"); params.append(int(area_id))
    rows=db.fetchall(
        f"""SELECT p.*,a.name AS area_name,COALESCE(NULLIF(w.display_name,''),CAST(p.user_id AS TEXT)) AS worker_name
        FROM shift_plans p LEFT JOIN areas a ON a.id=p.area_id
        LEFT JOIN workers w ON w.chat_id=p.chat_id AND w.user_id=p.user_id
        WHERE {' AND '.join(where)} ORDER BY p.planned_start,p.user_id""", tuple(params))
    return [dict(r) for r in rows]


def attendance_summary(
    chat_id: int, start_date: str, end_date: str, user_id: int | None = None, area_id: int | None = None
) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return []
    if end < start or (end-start).days > 370:
        return []
    start_text=f"{start} 00:00:00"; end_text=f"{end+timedelta(days=1)} 00:00:00"
    workers: dict[int, dict] = {}
    worker_rows=db.fetchall("SELECT user_id,display_name FROM workers WHERE chat_id=?", (scope,))
    for r in worker_rows:
        workers[int(r["user_id"])]= {
            "user_id":int(r["user_id"]), "worker_name":str(r["display_name"] or r["user_id"]),
            "planned_shifts":0,"completed_plans":0,"missed_shifts":0,"actual_shifts":0,
            "worked_minutes":0.0,"late_count":0,"late_minutes":0.0,
            "early_departure_count":0,"early_departure_minutes":0.0,"overtime_minutes":0.0,
        }
    plan_where=["p.chat_id=?","p.planned_start>=?","p.planned_start<?","p.status!='cancelled'"]
    plan_params: list[object]=[scope,start_text,end_text]
    if user_id is not None: plan_where.append("p.user_id=?"); plan_params.append(int(user_id))
    if area_id is not None: plan_where.append("p.area_id=?"); plan_params.append(int(area_id))
    plans=db.fetchall(
        f"""SELECT p.user_id,p.status,p.planned_end,s.start_deviation_minutes,s.end_deviation_minutes,s.id AS shift_id
        FROM shift_plans p LEFT JOIN worker_shifts s ON s.plan_id=p.id WHERE {' AND '.join(plan_where)}""", tuple(plan_params))
    now=datetime.now()
    for row in plans:
        uid=int(row["user_id"]); item=workers.setdefault(uid,{
            "user_id":uid,"worker_name":str(uid),"planned_shifts":0,"completed_plans":0,"missed_shifts":0,
            "actual_shifts":0,"worked_minutes":0.0,"late_count":0,"late_minutes":0.0,
            "early_departure_count":0,"early_departure_minutes":0.0,"overtime_minutes":0.0})
        item["planned_shifts"]+=1
        if row["shift_id"]: item["completed_plans"]+=1
        elif datetime.fromisoformat(str(row["planned_end"])) < now: item["missed_shifts"]+=1
        sd=row["start_deviation_minutes"]
        ed=row["end_deviation_minutes"]
        if sd is not None and float(sd)>0.5:
            item["late_count"]+=1; item["late_minutes"]+=float(sd)
        if ed is not None and float(ed)<-0.5:
            item["early_departure_count"]+=1; item["early_departure_minutes"]+=abs(float(ed))
        if ed is not None and float(ed)>0.5: item["overtime_minutes"]+=float(ed)
    shift_where=["s.chat_id=?","s.started_at>=?","s.started_at<?"]
    shift_params: list[object]=[scope,start_text,end_text]
    if user_id is not None: shift_where.append("s.user_id=?"); shift_params.append(int(user_id))
    if area_id is not None: shift_where.append("s.area_id=?"); shift_params.append(int(area_id))
    shifts=db.fetchall(
        f"""SELECT s.user_id,COUNT(*) AS shift_count,SUM(
            MAX(0,(julianday(COALESCE(s.ended_at,CURRENT_TIMESTAMP))-julianday(s.started_at))*1440)
        ) AS worked_minutes FROM worker_shifts s WHERE {' AND '.join(shift_where)} GROUP BY s.user_id""", tuple(shift_params))
    for row in shifts:
        uid=int(row["user_id"]); item=workers.setdefault(uid,{
            "user_id":uid,"worker_name":str(uid),"planned_shifts":0,"completed_plans":0,"missed_shifts":0,
            "actual_shifts":0,"worked_minutes":0.0,"late_count":0,"late_minutes":0.0,
            "early_departure_count":0,"early_departure_minutes":0.0,"overtime_minutes":0.0})
        item["actual_shifts"]=int(row["shift_count"] or 0); item["worked_minutes"]=round(float(row["worked_minutes"] or 0),1)
    result=[v for uid,v in workers.items() if user_id is None or uid==int(user_id)]
    return sorted(result,key=lambda x:(-x["worked_minutes"],x["worker_name"]))


def attendance_detail_rows(
    chat_id: int, start_date: str, end_date: str, user_id: int | None = None, area_id: int | None = None
) -> list[dict]:
    scope=resolve_scope_chat_id(chat_id)
    try:
        start=date.fromisoformat(start_date); end=date.fromisoformat(end_date)
    except ValueError: return []
    where=["p.chat_id=?","p.planned_start>=?","p.planned_start<?"]
    params: list[object]=[scope,f"{start} 00:00:00",f"{end+timedelta(days=1)} 00:00:00"]
    if user_id is not None: where.append("p.user_id=?"); params.append(int(user_id))
    if area_id is not None: where.append("p.area_id=?"); params.append(int(area_id))
    rows=db.fetchall(
        f"""SELECT p.id,p.user_id,COALESCE(NULLIF(w.display_name,''),CAST(p.user_id AS TEXT)) AS worker_name,
        a.name AS area_name,p.planned_start,p.planned_end,p.status AS plan_status,
        s.started_at,s.ended_at,s.status AS shift_status,s.start_deviation_minutes,s.end_deviation_minutes,
        CASE WHEN s.id IS NULL AND p.status='planned' AND p.planned_end<CURRENT_TIMESTAMP THEN 1 ELSE 0 END AS missed
        FROM shift_plans p LEFT JOIN worker_shifts s ON s.plan_id=p.id
        LEFT JOIN workers w ON w.chat_id=p.chat_id AND w.user_id=p.user_id
        LEFT JOIN areas a ON a.id=p.area_id WHERE {' AND '.join(where)} ORDER BY p.planned_start,p.user_id""", tuple(params))
    return [dict(r) for r in rows]


def queue_overdue_inventory_approval_escalations(now: datetime | None = None) -> int:
    now=now or datetime.now()
    sessions=[dict(r) for r in db.fetchall(
        """SELECT s.*,a.name AS area_name,COALESCE(NULLIF(w.display_name,''),CAST(s.created_by AS TEXT)) AS creator_name
        FROM inventory_sessions s LEFT JOIN areas a ON a.id=s.area_id
        LEFT JOIN workers w ON w.chat_id=s.chat_id AND w.user_id=s.created_by
        WHERE s.status='submitted' AND s.submitted_at IS NOT NULL"""
    )]
    created=0
    for session in sessions:
        try: submitted=datetime.fromisoformat(str(session["submitted_at"]))
        except ValueError: continue
        elapsed=max(0,(now-submitted).total_seconds()/60)
        for recipient in _approval_recipient_ids(int(session["chat_id"]), int(session["area_id"])):
            if int(recipient)==int(session.get("created_by") or 0) and not is_tenant_admin(int(session["chat_id"]), recipient): continue
            prefs=get_notification_preferences(int(session["chat_id"]),recipient)
            if not prefs.get("approval_reminders_enabled",True): continue
            max_level=max(0,int(prefs.get("max_reminders") or 0))
            if max_level<=0: continue
            first=max(5,int(prefs.get("reminder_after_minutes") or 60))
            repeat=max(5,int(prefs.get("repeat_every_minutes") or 120))
            existing=db.fetchone(
                "SELECT COALESCE(MAX(escalation_level),0) AS level FROM inventory_approval_escalations WHERE session_id=? AND recipient_user_id=?",
                (int(session["id"]),int(recipient)))
            next_level=int(existing["level"] or 0)+1
            if next_level>max_level: continue
            due_at=first+(next_level-1)*repeat
            if elapsed<due_at: continue
            urgent=next_level>=max_level
            item_id=create_inbox_item(
                int(session["chat_id"]),int(recipient),"inventory_approval_reminder",
                ("Срочно: " if urgent else "Напоминание: ")+f"инвентаризация №{session['id']} ждёт решения",
                f"Ожидает {int(elapsed)} мин. Площадка: {session.get('area_name') or 'не указана'}. Автор: {session.get('creator_name') or session.get('created_by')}",
                "inventory_session",int(session["id"]),deduplicate=False,priority="urgent" if urgent else "high",
            )
            with db.connect() as conn:
                try:
                    conn.execute(
                        "INSERT INTO inventory_approval_escalations(chat_id,session_id,recipient_user_id,escalation_level,inbox_item_id) VALUES(?,?,?,?,?)",
                        (int(session["chat_id"]),int(session["id"]),int(recipient),next_level,item_id or None))
                    conn.commit(); created+=1
                except Exception:
                    conn.rollback()
    return created


# --- Отделы, ограниченные рабочие контуры и иерархия доступа step70 ---

DEPARTMENT_OPERATION_KEYS = {
    "production", "material_in", "material_out", "energy", "assembly",
    "movement", "transfer_to_assembly", "shipment", "shipment_client",
    "shipment_fulfillment", "return", "stock_in", "stock_out", "write_off", "inventory_adjust",
    "shifts",
}

DEPARTMENT_ROLE_LEVELS = {"viewer": 10, "operator": 20, "editor": 30, "manager": 50}
DEPARTMENT_ROLE_NAMES = {10: "Просмотр", 20: "Ввод данных", 30: "Ввод и исправление", 50: "Руководитель"}


def is_system_admin_id(user_id: int | None) -> bool:
    """Platform-level owner only. Tenant admins are separate."""
    return is_primary_owner_id(user_id)


def _department_scope(chat_id: int) -> int:
    return resolve_scope_chat_id(int(chat_id))


def _department_row(department_id: int) -> dict | None:
    row = db.fetchone("SELECT * FROM departments WHERE id=? AND is_archived=0", (int(department_id),))
    return dict(row) if row else None


def list_departments(chat_id: int, user_id: int | None = None, *, manageable_only: bool = False) -> list[dict]:
    scope = _department_scope(chat_id)
    params: list[object] = [scope]
    where = ["d.chat_id=?", "d.is_archived=0"]
    if user_id is not None and not is_tenant_admin(scope, user_id):
        where.append("EXISTS(SELECT 1 FROM department_members dm WHERE dm.department_id=d.id AND dm.user_id=? AND dm.is_active=1" + (" AND dm.role_level>=50" if manageable_only else "") + ")")
        params.append(int(user_id))
    rows = db.fetchall(
        f"""
        SELECT d.*,
               COALESCE((SELECT COUNT(*) FROM department_members dm WHERE dm.department_id=d.id AND dm.is_active=1),0) AS member_count
        FROM departments d
        WHERE {' AND '.join(where)}
        ORDER BY d.name
        """,
        tuple(params),
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        item["operations"] = list_department_operation_rules(int(row["id"]))
        item["entities"] = list_department_entity_rules(int(row["id"]))
        item["members"] = list_department_members(int(row["id"]))
        result.append(item)
    return result


def save_department(chat_id: int, actor_user_id: int, name: str, description: str = "", department_id: int | None = None) -> tuple[bool, str, int | None]:
    scope = _department_scope(chat_id)
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Создавать и изменять отделы может только администратор фирмы.", None
    clean_name = (name or "").strip()
    key = normalize_key(clean_name)
    if not key:
        return False, "Укажите название отдела.", None
    if department_id:
        row = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (int(department_id), scope))
        if not row:
            return False, "Отдел не найден.", None
        conflict = db.fetchone("SELECT id FROM departments WHERE chat_id=? AND normalized=? AND id<>? AND is_archived=0", (scope, key, int(department_id)))
        if conflict:
            return False, "Отдел с таким названием уже есть.", None
        db.execute("UPDATE departments SET name=?,normalized=?,description=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (clean_name, key, (description or "").strip(), int(department_id)))
        return True, "Отдел обновлён.", int(department_id)
    existing = db.fetchone("SELECT id,is_archived FROM departments WHERE chat_id=? AND normalized=?", (scope, key))
    if existing and not int(existing["is_archived"] or 0):
        return False, "Отдел с таким названием уже есть.", None
    if existing:
        db.execute("UPDATE departments SET name=?,description=?,is_archived=0,created_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (clean_name, (description or "").strip(), int(actor_user_id), int(existing["id"])))
        return True, "Отдел восстановлен.", int(existing["id"])
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO departments(chat_id,name,normalized,description,created_by) VALUES(?,?,?,?,?)", (scope, clean_name, key, (description or "").strip(), int(actor_user_id)))
        conn.commit()
        department_id = int(cur.lastrowid)
    return True, "Отдел создан.", department_id


def _disable_department_only_account_access(scope: int, user_id: int) -> None:
    """Закрывает доступ, выданный только через отдел, если активных отделов больше нет."""
    if is_tenant_admin(scope, user_id):
        return
    remaining = db.fetchone(
        """
        SELECT 1
        FROM department_members dm
        JOIN departments d ON d.id=dm.department_id AND d.is_archived=0
        WHERE d.chat_id=? AND dm.user_id=? AND dm.is_active=1
        LIMIT 1
        """,
        (int(scope), int(user_id)),
    )
    if remaining:
        return
    account = get_account_by_scope(int(scope))
    if not account:
        return
    # Не затрагиваем старые назначения должностей и управляющих.
    db.execute(
        """
        UPDATE account_user_access
        SET can_view=0,can_submit=0,updated_at=CURRENT_TIMESTAMP
        WHERE account_id=? AND user_id=? AND job_title_id IS NULL AND COALESCE(can_manage,0)=0
        """,
        (int(account.id), int(user_id)),
    )


def archive_department(chat_id: int, actor_user_id: int, department_id: int) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Удалять отдел может только администратор фирмы."
    row = db.fetchone("SELECT id,normalized FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (int(department_id), scope))
    if not row:
        return False, "Отдел не найден."
    member_ids = [int(item["user_id"]) for item in db.fetchall("SELECT user_id FROM department_members WHERE department_id=? AND is_active=1", (int(department_id),))]
    db.execute("UPDATE departments SET is_archived=1,normalized=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (f"{row['normalized']} archived {department_id}", int(department_id)))
    for member_id in member_ids:
        _disable_department_only_account_access(scope, member_id)
    return True, "Отдел отключён."


def list_department_operation_rules(department_id: int) -> list[dict]:
    return [dict(row) for row in db.fetchall("SELECT * FROM department_operation_rules WHERE department_id=? ORDER BY operation_key", (int(department_id),))]


def set_department_operation_rule(chat_id: int, actor_user_id: int, department_id: int, operation_key: str, *, can_view: bool, can_submit: bool, can_edit: bool) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    department = _department_row(department_id)
    operation_key = (operation_key or "").strip()
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Настраивать возможности отдела может только администратор фирмы."
    if operation_key not in DEPARTMENT_OPERATION_KEYS:
        return False, "Неизвестное действие."
    view = bool(can_view or can_submit or can_edit)
    submit = bool(can_submit or can_edit)
    db.execute(
        """
        INSERT INTO department_operation_rules(department_id,operation_key,can_view,can_submit,can_edit,updated_at)
        VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(department_id,operation_key) DO UPDATE SET
          can_view=excluded.can_view,can_submit=excluded.can_submit,can_edit=excluded.can_edit,updated_at=CURRENT_TIMESTAMP
        """,
        (int(department_id), operation_key, 1 if view else 0, 1 if submit else 0, 1 if can_edit else 0),
    )
    return True, "Доступ отдела сохранён."


def delete_department_operation_rule(chat_id: int, actor_user_id: int, department_id: int, operation_key: str) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Настраивать возможности отдела может только администратор фирмы."
    department = _department_row(department_id)
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    db.execute("DELETE FROM department_operation_rules WHERE department_id=? AND operation_key=?", (int(department_id), (operation_key or "").strip()))
    return True, "Действие убрано из отдела."


def list_department_entity_rules(department_id: int) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT der.*,e.name AS entity_name,e.default_unit
        FROM department_entity_rules der
        JOIN entities e ON e.id=der.entity_id AND e.is_archived=0
        WHERE der.department_id=?
        ORDER BY der.operation_key,e.name
        """,
        (int(department_id),),
    )
    return [dict(row) for row in rows]


def set_department_entity_rule(chat_id: int, actor_user_id: int, department_id: int, operation_key: str, entity_type: str, entity_id: int, *, can_view: bool = True, can_submit: bool = True) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    department = _department_row(department_id)
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Назначать позиции отделу может только администратор фирмы."
    operation_key = (operation_key or "").strip()
    if operation_key not in DEPARTMENT_OPERATION_KEYS:
        return False, "Неизвестное действие."
    entity = db.fetchone("SELECT id,entity_type FROM entities WHERE id=? AND chat_id=? AND is_archived=0", (int(entity_id), scope))
    if not entity or str(entity["entity_type"]) != str(entity_type):
        return False, "Позиция не найдена."
    db.execute(
        """
        INSERT INTO department_entity_rules(department_id,operation_key,entity_type,entity_id,can_view,can_submit,updated_at)
        VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(department_id,operation_key,entity_type,entity_id) DO UPDATE SET
          can_view=excluded.can_view,can_submit=excluded.can_submit,updated_at=CURRENT_TIMESTAMP
        """,
        (int(department_id), operation_key, str(entity_type), int(entity_id), 1 if (can_view or can_submit) else 0, 1 if can_submit else 0),
    )
    return True, "Позиция назначена отделу."


def delete_department_entity_rule(chat_id: int, actor_user_id: int, department_id: int, operation_key: str, entity_id: int) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    if not is_tenant_admin(scope, actor_user_id):
        return False, "Назначать позиции отделу может только администратор фирмы."
    department = _department_row(department_id)
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    db.execute("DELETE FROM department_entity_rules WHERE department_id=? AND operation_key=? AND entity_id=?", (int(department_id), (operation_key or "").strip(), int(entity_id)))
    return True, "Позиция убрана из отдела."


def list_department_members(department_id: int) -> list[dict]:
    rows = db.fetchall("SELECT * FROM department_members WHERE department_id=? AND is_active=1 ORDER BY role_level DESC,display_name,user_id", (int(department_id),))
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["operation_keys"] = json.loads(item.get("operation_keys_json") or "[]")
        except Exception:
            item["operation_keys"] = []
        item["role_name"] = DEPARTMENT_ROLE_NAMES.get(int(item.get("role_level") or 20), "Участник")
        result.append(item)
    return result


def _member_row(department_id: int, user_id: int) -> dict | None:
    row = db.fetchone("SELECT * FROM department_members WHERE department_id=? AND user_id=? AND is_active=1", (int(department_id), int(user_id)))
    return dict(row) if row else None


def department_actor_level(department_id: int, user_id: int | None) -> int:
    department=_department_row(department_id)
    if department and is_tenant_admin(int(department["chat_id"]), user_id):
        return 100
    if not user_id:
        return 0
    row = _member_row(department_id, int(user_id))
    return int(row.get("role_level") or 0) if row else 0


def _member_operation_subset(member: dict | None) -> set[str] | None:
    if not member:
        return set()
    try:
        values = {str(x) for x in json.loads(member.get("operation_keys_json") or "[]") if str(x) in DEPARTMENT_OPERATION_KEYS}
    except Exception:
        values = set()
    return values or None


def save_department_member(chat_id: int, actor_user_id: int, department_id: int, member_user_id: int, display_name: str, role_level: int, operation_keys: list[str] | None = None) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    department = _department_row(department_id)
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    actor_level = department_actor_level(department_id, actor_user_id)
    if actor_level < 50:
        return False, "Вы не можете выдавать доступ к этому отделу."
    role_level = int(role_level)
    if role_level not in DEPARTMENT_ROLE_NAMES:
        return False, "Неизвестный уровень доступа."
    if role_level > actor_level:
        return False, "Нельзя выдать уровень выше собственного."
    if int(member_user_id) <= 0:
        return False, "Укажите Telegram ID сотрудника."
    allowed_department_ops = {str(r["operation_key"]) for r in list_department_operation_rules(department_id)}
    cleaned_ops = {str(x) for x in (operation_keys or []) if str(x) in allowed_department_ops}
    if not is_tenant_admin(scope, actor_user_id):
        actor = _member_row(department_id, actor_user_id)
        actor_subset = _member_operation_subset(actor)
        if actor_subset is not None:
            # Пустой список означает «все действия отдела». Ограниченный руководитель
            # не может превратить его в полный доступ: наследуем только его собственный набор.
            if not cleaned_ops:
                cleaned_ops = set(actor_subset)
            elif not cleaned_ops.issubset(actor_subset):
                return False, "Нельзя выдать действия, которых нет у вас."
    db.execute(
        """
        INSERT INTO department_members(department_id,user_id,display_name,role_level,operation_keys_json,is_active,granted_by,updated_at)
        VALUES(?,?,?,?,?,1,?,CURRENT_TIMESTAMP)
        ON CONFLICT(department_id,user_id) DO UPDATE SET
          display_name=excluded.display_name,role_level=excluded.role_level,
          operation_keys_json=excluded.operation_keys_json,is_active=1,granted_by=excluded.granted_by,updated_at=CURRENT_TIMESTAMP
        """,
        (int(department_id), int(member_user_id), (display_name or "").strip(), role_level, json.dumps(sorted(cleaned_ops), ensure_ascii=False), int(actor_user_id)),
    )
    account = get_account_by_scope(scope)
    if account:
        db.execute(
            """
            INSERT INTO account_user_access(account_id,user_id,job_title_id,can_manage,can_view,can_submit,updated_at)
            VALUES(?,?,NULL,0,1,1,CURRENT_TIMESTAMP)
            ON CONFLICT(account_id,user_id) DO UPDATE SET can_view=1,can_submit=1,updated_at=CURRENT_TIMESTAMP
            """,
            (account.id, int(member_user_id)),
        )
    return True, "Доступ сотрудника сохранён."


def archive_department_member(chat_id: int, actor_user_id: int, department_id: int, member_user_id: int) -> tuple[bool, str]:
    scope = _department_scope(chat_id)
    department = _department_row(department_id)
    if not department or int(department["chat_id"]) != scope:
        return False, "Отдел не найден."
    actor_level = department_actor_level(department_id, actor_user_id)
    target = _member_row(department_id, member_user_id)
    if actor_level < 50 or not target:
        return False, "Доступ не найден."
    if int(target.get("role_level") or 0) > actor_level:
        return False, "Нельзя отключить сотрудника с более высоким уровнем."
    if int(member_user_id) == int(actor_user_id) and not is_tenant_admin(scope, actor_user_id):
        return False, "Руководитель не может отключить собственный доступ."
    db.execute("UPDATE department_members SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE department_id=? AND user_id=?", (int(department_id), int(member_user_id)))
    _disable_department_only_account_access(scope, int(member_user_id))
    return True, "Доступ к отделу отключён."


def user_department_memberships(chat_id: int, user_id: int | None) -> list[dict]:
    if not user_id:
        return []
    scope = _department_scope(chat_id)
    rows = db.fetchall(
        """
        SELECT dm.*,d.name AS department_name,d.chat_id
        FROM department_members dm
        JOIN departments d ON d.id=dm.department_id AND d.is_archived=0
        WHERE d.chat_id=? AND dm.user_id=? AND dm.is_active=1
        ORDER BY d.name
        """,
        (scope, int(user_id)),
    )
    result = []
    for row in rows:
        item = dict(row)
        item["operation_keys"] = sorted(_member_operation_subset(item) or [])
        item["role_name"] = DEPARTMENT_ROLE_NAMES.get(int(item.get("role_level") or 20), "Участник")
        result.append(item)
    return result


def user_has_department_membership(chat_id: int, user_id: int | None) -> bool:
    return bool(user_department_memberships(chat_id, user_id))


def department_operation_allowed(chat_id: int, user_id: int | None, operation_key: str, action: str = "view", entity_type: str | None = None, entity_id: int | None = None) -> bool | None:
    if is_tenant_admin(chat_id, user_id):
        return True
    memberships = user_department_memberships(chat_id, user_id)
    if not memberships:
        return None
    operation_key = (operation_key or "").strip()
    required_level = {"view": 10, "submit": 20, "edit": 30, "grant": 50}.get(action, 10)
    rule_column = {"view": "can_view", "submit": "can_submit", "edit": "can_edit"}.get(action, "can_view")
    for membership in memberships:
        if int(membership.get("role_level") or 0) < required_level:
            continue
        subset = _member_operation_subset(membership)
        if subset is not None and operation_key not in subset:
            continue
        rule = db.fetchone(f"SELECT {rule_column} AS allowed FROM department_operation_rules WHERE department_id=? AND operation_key=?", (int(membership["department_id"]), operation_key))
        if not rule or not int(rule["allowed"] or 0):
            continue
        if entity_id is None:
            return True
        entity_rules = db.fetchall("SELECT entity_id,can_view,can_submit FROM department_entity_rules WHERE department_id=? AND operation_key=?", (int(membership["department_id"]), operation_key))
        if not entity_rules:
            continue
        for entity_rule in entity_rules:
            if int(entity_rule["entity_id"]) != int(entity_id):
                continue
            if action in {"submit", "edit"} and not int(entity_rule["can_submit"] or 0):
                continue
            if action == "view" and not int(entity_rule["can_view"] or 0):
                continue
            return True
    return False


def department_work_access_for_user(chat_id: int, user_id: int | None) -> list[dict]:
    """Возвращает только разрешённые действия и позиции без состава отдела и списка людей."""
    if not user_id or is_tenant_admin(chat_id, user_id):
        return []
    memberships = user_department_memberships(chat_id, user_id)
    if not memberships:
        return []
    merged: dict[str, dict] = {}
    excluded = {"inventory_adjust", "shifts"}
    for membership in memberships:
        if int(membership.get("role_level") or 0) < 20:
            continue
        subset = _member_operation_subset(membership)
        rules = db.fetchall(
            "SELECT operation_key,can_view,can_submit,can_edit FROM department_operation_rules WHERE department_id=?",
            (int(membership["department_id"]),),
        )
        for rule in rules:
            operation_key = str(rule["operation_key"])
            if operation_key in excluded or operation_key not in DEPARTMENT_OPERATION_KEYS:
                continue
            if subset is not None and operation_key not in subset:
                continue
            if not int(rule["can_submit"] or 0):
                continue
            target = merged.setdefault(operation_key, {
                "operation_key": operation_key,
                "can_submit": True,
                "can_edit": bool(rule["can_edit"]),
                "departments": set(),
                "entities": {},
            })
            target["can_edit"] = bool(target["can_edit"] or rule["can_edit"])
            target["departments"].add(str(membership.get("department_name") or ""))
            entity_rows = db.fetchall(
                """
                SELECT der.entity_id,der.entity_type,e.name,e.default_unit,
                       (SELECT ec.code FROM entity_codes ec WHERE ec.entity_id=e.id ORDER BY ec.is_primary DESC,ec.id LIMIT 1) AS code
                FROM department_entity_rules der
                JOIN entities e ON e.id=der.entity_id AND e.is_archived=0
                WHERE der.department_id=? AND der.operation_key=? AND der.can_submit=1
                ORDER BY e.name
                """,
                (int(membership["department_id"]), operation_key),
            )
            for entity in entity_rows:
                target["entities"][int(entity["entity_id"])] = {
                    "id": int(entity["entity_id"]),
                    "type": str(entity["entity_type"]),
                    "name": str(entity["name"]),
                    "unit": str(entity["default_unit"] or "шт"),
                    "code": str(entity["code"] or ""),
                }
    result: list[dict] = []
    for operation_key in sorted(merged):
        item = merged[operation_key]
        result.append({
            "operation_key": operation_key,
            "can_submit": True,
            "can_edit": bool(item["can_edit"]),
            "departments": sorted(name for name in item["departments"] if name),
            "entities": sorted(item["entities"].values(), key=lambda value: value["name"].lower()),
        })
    return result


def visible_entity_ids_for_user(chat_id: int, user_id: int | None) -> set[int] | None:
    if is_tenant_admin(chat_id, user_id):
        return None
    memberships = user_department_memberships(chat_id, user_id)
    if not memberships:
        return None
    ids: set[int] = set()
    for membership in memberships:
        subset = _member_operation_subset(membership)
        params: list[object] = [int(membership["department_id"])]
        where = ["department_id=?", "can_view=1"]
        if subset is not None:
            if not subset:
                continue
            marks = ",".join("?" for _ in subset)
            where.append(f"operation_key IN ({marks})")
            params.extend(sorted(subset))
        rows = db.fetchall(f"SELECT DISTINCT entity_id FROM department_entity_rules WHERE {' AND '.join(where)}", tuple(params))
        ids.update(int(row["entity_id"]) for row in rows)
    return ids


def visible_entities_for_user(chat_id: int, user_id: int | None) -> list[Entity]:
    ids = visible_entity_ids_for_user(chat_id, user_id)
    entities = list_entities(chat_id)
    if ids is None:
        return entities
    return [entity for entity in entities if entity.id in ids]


def department_permissions_for_user(chat_id: int, user_id: int | None) -> dict[str, bool] | None:
    memberships = user_department_memberships(chat_id, user_id)
    if not memberships:
        return None
    operation_to_permission = {
        "production": "production", "material_in": "material", "material_out": "material",
        "energy": "energy", "assembly": "assembly", "movement": "movement", "transfer_to_assembly": "movement",
        "shipment": "shipment", "shipment_client": "shipment", "shipment_fulfillment": "fulfillment", "return": "returns",
        "stock_in": "stock", "stock_out": "stock", "write_off": "stock", "inventory_adjust": "stock", "shifts": "shifts",
    }
    result = {key: False for key in PERMISSION_KEYS}
    for operation_key, permission_key in operation_to_permission.items():
        allowed = department_operation_allowed(chat_id, user_id, operation_key, "view")
        submit = department_operation_allowed(chat_id, user_id, operation_key, "submit")
        edit = department_operation_allowed(chat_id, user_id, operation_key, "edit")
        if allowed or submit or edit:
            result[permission_key] = True
        if edit:
            result["edit"] = True
    return result


# Step 82: tenant owner/admin and department permissions are separate from platform owner.
def _account_access_row(account_id: int, user_id: int | None) -> dict | None:
    if not user_id:
        return None
    row = db.fetchone("SELECT job_title_id,can_manage,can_view,can_submit FROM account_user_access WHERE account_id=? AND user_id=?", (int(account_id), int(user_id)))
    return dict(row) if row else None

def is_tenant_admin(chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    scope = resolve_scope_chat_id(chat_id)
    account = get_active_account(chat_id) or get_account_by_scope(scope)
    if not account:
        return False
    if int(account.owner_user_id) == int(user_id):
        return True
    row = _account_access_row(account.id, user_id)
    return bool(row and row.get("can_manage"))

def tenant_admin_user_ids(chat_id: int) -> list[int]:
    scope = resolve_scope_chat_id(chat_id)
    account = get_active_account(chat_id) or get_account_by_scope(scope)
    if not account:
        return []
    ids = {int(account.owner_user_id)}
    rows = db.fetchall("SELECT user_id FROM account_user_access WHERE account_id=? AND can_manage=1", (int(account.id),))
    ids.update(int(r["user_id"]) for r in rows if r["user_id"])
    return sorted(ids)

def user_permissions_current_context(chat_id: int, user_id: int | None) -> dict[str, bool]:
    scope = resolve_scope_chat_id(chat_id)
    account = get_active_account(chat_id) or get_account_by_scope(scope)
    if account and user_id:
        if int(account.owner_user_id) == int(user_id):
            return full_permissions()
        row = _account_access_row(account.id, user_id)
        if row and row.get("can_manage"):
            return full_permissions()
    department_permissions = department_permissions_for_user(scope, user_id)
    if department_permissions is not None:
        return department_permissions
    if account and user_id:
        row = _account_access_row(account.id, user_id)
        if row:
            return _permissions_from_job_id(int(row["job_title_id"]) if row.get("job_title_id") else None)
    return worker_permissions(scope, user_id or 0)

def user_can_manage_current_context(chat_id: int, user_id: int | None) -> bool:
    return is_tenant_admin(chat_id, user_id)

def user_can_manage_departments(chat_id: int, user_id: int | None) -> bool:
    if is_tenant_admin(chat_id, user_id):
        return True
    return any(int(item.get("role_level") or 0) >= 50 for item in user_department_memberships(chat_id, user_id))

# --- Step 72: удобный и безопасный ввод ------------------------------------

def get_operation_by_client_request(chat_id: int, user_id: int, client_request_id: str | None) -> dict | None:
    key = str(client_request_id or "").strip()
    if not key:
        return None
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT o.id,o.chat_id,o.group_chat_id,o.area_id,o.from_area_id,o.to_area_id,
               o.user_id,o.operation_type,o.entity_type,o.entity_id,o.quantity,o.unit,
               o.destination_type,o.storage_place,o.raw_text,o.client_request_id,
               o.source_channel,o.created_at,e.name AS entity_name,
               a.name AS area_name,fa.name AS from_area_name,ta.name AS to_area_name
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        LEFT JOIN areas fa ON fa.id=o.from_area_id
        LEFT JOIN areas ta ON ta.id=o.to_area_id
        WHERE o.chat_id=? AND o.user_id=? AND o.client_request_id=?
        LIMIT 1
        """,
        (scope, int(user_id), key),
    )
    return dict(row) if row else None


def list_recent_operation_templates(chat_id: int, user_id: int, limit: int = 8) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT o.id,o.operation_type,o.entity_type,o.entity_id,o.quantity,o.unit,
               o.area_id,o.from_area_id,o.to_area_id,o.destination_type,o.storage_place,
               o.raw_text,o.created_at,e.name AS entity_name,
               a.name AS area_name,fa.name AS from_area_name,ta.name AS to_area_name
        FROM operations o
        LEFT JOIN operation_corrections oc ON oc.original_operation_id=o.id
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        LEFT JOIN areas fa ON fa.id=o.from_area_id
        LEFT JOIN areas ta ON ta.id=o.to_area_id
        WHERE o.chat_id=? AND o.user_id=? AND oc.id IS NULL AND e.is_archived=0
        ORDER BY o.created_at DESC,o.id DESC
        LIMIT ?
        """,
        (scope, int(user_id), max(1, min(int(limit), 20))),
    )
    result: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        item = dict(row)
        key = (
            item.get("operation_type"), item.get("entity_type"), item.get("entity_id"),
            item.get("area_id"), item.get("from_area_id"), item.get("to_area_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[: max(1, min(int(limit), 12))]


def list_operation_presets(chat_id: int, user_id: int) -> list[dict]:
    scope = resolve_scope_chat_id(chat_id)
    rows = db.fetchall(
        """
        SELECT p.*,e.name AS entity_name,a.name AS area_name,
               fa.name AS from_area_name,ta.name AS to_area_name
        FROM operation_presets p
        JOIN entities e ON e.id=p.entity_id AND e.is_archived=0
        LEFT JOIN areas a ON a.id=p.area_id
        LEFT JOIN areas fa ON fa.id=p.from_area_id
        LEFT JOIN areas ta ON ta.id=p.to_area_id
        WHERE p.chat_id=? AND p.user_id=?
        ORDER BY p.usage_count DESC,COALESCE(p.last_used_at,p.updated_at) DESC,p.id DESC
        """,
        (scope, int(user_id)),
    )
    return [dict(row) for row in rows]


def save_operation_preset(
    chat_id: int,
    user_id: int,
    *,
    name: str,
    operation_type: str,
    entity_type: str,
    entity_id: int,
    quantity: float = 0,
    unit: str = "шт",
    area_id: int | None = None,
    from_area_id: int | None = None,
    to_area_id: int | None = None,
    destination_type: str = "",
    storage_place: str = "",
    note: str = "",
) -> tuple[bool, str]:
    scope = resolve_scope_chat_id(chat_id)
    clean_name = " ".join(str(name or "").split()).strip()[:80]
    if not clean_name:
        return False, "Укажите название быстрого действия."
    entity = get_entity(int(entity_id))
    if not entity or entity.chat_id != scope or entity.entity_type != entity_type:
        return False, "Позиция не найдена."
    existing = db.fetchone(
        "SELECT id FROM operation_presets WHERE chat_id=? AND user_id=? AND name=?",
        (scope, int(user_id), clean_name),
    )
    params = (
        operation_type, entity_type, int(entity_id), max(0.0, float(quantity or 0)), unit or entity.default_unit or "шт",
        area_id, from_area_id, to_area_id, destination_type or "", storage_place or "", str(note or "")[:500],
    )
    if existing:
        db.execute(
            """
            UPDATE operation_presets
            SET operation_type=?,entity_type=?,entity_id=?,quantity=?,unit=?,area_id=?,from_area_id=?,to_area_id=?,
                destination_type=?,storage_place=?,note=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND chat_id=? AND user_id=?
            """,
            params + (int(existing["id"]), scope, int(user_id)),
        )
        return True, "Быстрое действие обновлено."
    count_row = db.fetchone("SELECT COUNT(*) AS n FROM operation_presets WHERE chat_id=? AND user_id=?", (scope, int(user_id)))
    if int(count_row["n"] if count_row else 0) >= 12:
        return False, "Можно сохранить не больше 12 быстрых действий. Удалите ненужное."
    db.execute(
        """
        INSERT INTO operation_presets(
            chat_id,user_id,name,operation_type,entity_type,entity_id,quantity,unit,area_id,from_area_id,to_area_id,
            destination_type,storage_place,note
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (scope, int(user_id), clean_name) + params,
    )
    return True, "Быстрое действие сохранено."


def delete_operation_preset(chat_id: int, user_id: int, preset_id: int) -> bool:
    scope = resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        "SELECT id FROM operation_presets WHERE id=? AND chat_id=? AND user_id=?",
        (int(preset_id), scope, int(user_id)),
    )
    if not row:
        return False
    db.execute("DELETE FROM operation_presets WHERE id=?", (int(preset_id),))
    return True


def touch_operation_preset(chat_id: int, user_id: int, preset_id: int) -> None:
    scope = resolve_scope_chat_id(chat_id)
    db.execute(
        """
        UPDATE operation_presets
        SET usage_count=usage_count+1,last_used_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND chat_id=? AND user_id=?
        """,
        (int(preset_id), scope, int(user_id)),
    )


def setup_health(chat_id: int) -> dict[str, object]:
    scope = resolve_scope_chat_id(chat_id)
    counts = {
        "areas": _count_table("areas", "WHERE chat_id=? AND is_archived=0", (scope,)),
        "departments": _count_table("departments", "WHERE chat_id=? AND is_archived=0", (scope,)),
        "workers": int((db.fetchone(
            """
            SELECT COUNT(DISTINCT user_id) AS n FROM (
              SELECT user_id FROM workers WHERE chat_id=? AND is_active=1
              UNION
              SELECT dm.user_id FROM department_members dm
              JOIN departments d ON d.id=dm.department_id
              WHERE d.chat_id=? AND d.is_archived=0 AND dm.is_active=1
            )
            """,
            (scope, scope),
        ) or {"n": 0})["n"]),
        "components": _count_table("entities", "WHERE chat_id=? AND entity_type='component' AND is_archived=0", (scope,)),
        "products": _count_table("entities", "WHERE chat_id=? AND entity_type='product' AND is_archived=0", (scope,)),
        "materials": _count_table("entities", "WHERE chat_id=? AND entity_type='material' AND is_archived=0", (scope,)),
        "risk_rules": _count_table("stock_alert_rules", "WHERE chat_id=? AND is_enabled=1", (scope,)),
    }
    composition = db.fetchone(
        """
        SELECT COUNT(DISTINCT pc.product_id) AS n
        FROM product_components pc JOIN entities e ON e.id=pc.product_id
        WHERE e.chat_id=? AND e.is_archived=0
        """,
        (scope,),
    )
    counts["products_with_composition"] = int(composition["n"] if composition else 0)
    checks = [
        {"key": "areas", "label": "Создана хотя бы одна площадка", "ok": counts["areas"] > 0},
        {"key": "positions", "label": "Созданы рабочие позиции", "ok": (counts["components"] + counts["products"] + counts["materials"]) > 0},
        {"key": "departments", "label": "Настроены отделы и права", "ok": counts["departments"] > 0},
        {"key": "workers", "label": "Добавлены сотрудники", "ok": counts["workers"] > 0},
        {"key": "composition", "label": "Для собираемых изделий указан состав", "ok": counts["products"] == 0 or counts["products_with_composition"] > 0},
        {"key": "risk_rules", "label": "Настроены критические остатки", "ok": counts["risk_rules"] > 0},
    ]
    ready = sum(1 for item in checks if item["ok"])
    return {"checks": checks, "counts": counts, "ready": ready, "total": len(checks)}


# --- Step 82: company sites, tenant audit and owner-safe inspection ----------
def tenant_audit(chat_id: int, actor_user_id: int, event_type: str, object_type: str = '', object_id: str = '', details: str = '', severity: str = 'info') -> None:
    scope = resolve_scope_chat_id(chat_id)
    db.execute('INSERT INTO tenant_audit_events(chat_id,actor_user_id,event_type,object_type,object_id,severity,details) VALUES(?,?,?,?,?,?,?)',
               (scope,int(actor_user_id),str(event_type)[:80],str(object_type)[:80],str(object_id)[:120],str(severity)[:20],str(details)[:2000]))

def list_company_sites(chat_id: int) -> list[dict]:
    scope=resolve_scope_chat_id(chat_id)
    return [dict(r) for r in db.fetchall('SELECT * FROM company_sites WHERE chat_id=? AND is_archived=0 ORDER BY settlement,name',(scope,))]

def create_company_site(chat_id: int, actor_user_id: int, settlement: str, name: str, address: str = '') -> tuple[bool,str,int|None]:
    if not is_tenant_admin(chat_id, actor_user_id): return False,'Нет права настройки организации.',None
    scope=resolve_scope_chat_id(chat_id); key=normalize_key(f'{settlement} {name}')
    if not key: return False,'Укажите название площадки.',None
    try:
        with db.connect() as conn:
            cur=conn.execute('INSERT INTO company_sites(chat_id,settlement,name,normalized,address,created_by) VALUES(?,?,?,?,?,?)',(scope,(settlement or '').strip(),(name or '').strip(),key,(address or '').strip(),int(actor_user_id)))
            site_id=int(cur.lastrowid); conn.commit()
        tenant_audit(scope,actor_user_id,'site_create','site',str(site_id),f'{settlement} / {name}')
        return True,'Площадка создана.',site_id
    except Exception:
        return False,'Такая площадка уже существует.',None

def bind_area_to_site(chat_id: int, actor_user_id: int, area_id: int, site_id: int|None) -> tuple[bool,str]:
    if not is_tenant_admin(chat_id,actor_user_id): return False,'Нет права настройки.'
    scope=resolve_scope_chat_id(chat_id)
    if not db.fetchone('SELECT id FROM areas WHERE id=? AND chat_id=? AND is_archived=0',(int(area_id),scope)): return False,'Участок не найден.'
    if site_id is not None and not db.fetchone('SELECT id FROM company_sites WHERE id=? AND chat_id=? AND is_archived=0',(int(site_id),scope)): return False,'Площадка не найдена.'
    db.execute('UPDATE areas SET site_id=? WHERE id=?',(int(site_id) if site_id else None,int(area_id)))
    tenant_audit(scope,actor_user_id,'area_site_bind','area',str(area_id),str(site_id or ''))
    return True,'Привязка сохранена.'

def list_storage_locations(chat_id: int) -> list[dict]:
    scope=resolve_scope_chat_id(chat_id)
    q="""SELECT l.*,s.name AS site_name,s.settlement,a.name AS area_name,d.name AS department_name
           FROM storage_locations l LEFT JOIN company_sites s ON s.id=l.site_id LEFT JOIN areas a ON a.id=l.area_id
           LEFT JOIN departments d ON d.id=l.department_id WHERE l.chat_id=? AND l.is_archived=0
           ORDER BY COALESCE(s.settlement,''),COALESCE(s.name,''),COALESCE(a.name,''),COALESCE(d.name,''),l.name"""
    return [dict(r) for r in db.fetchall(q,(scope,))]

def create_storage_location(chat_id:int, actor_user_id:int, name:str, site_id:int|None=None, area_id:int|None=None, department_id:int|None=None, code:str='')->tuple[bool,str,int|None]:
    if not is_tenant_admin(chat_id,actor_user_id): return False,'Нет права настройки.',None
    scope=resolve_scope_chat_id(chat_id); key=normalize_key(name)
    if not key: return False,'Укажите название места хранения.',None
    for table,obj_id in (('company_sites',site_id),('areas',area_id),('departments',department_id)):
        if obj_id is not None and not db.fetchone(f'SELECT id FROM {table} WHERE id=? AND chat_id=?',(int(obj_id),scope)): return False,'Выбрано место из другого учёта.',None
    try:
        with db.connect() as conn:
            cur=conn.execute('INSERT INTO storage_locations(chat_id,site_id,area_id,department_id,name,normalized,code,created_by) VALUES(?,?,?,?,?,?,?,?)',(scope,site_id,area_id,department_id,name.strip(),key,(code or '').strip(),int(actor_user_id)))
            lid=int(cur.lastrowid); conn.commit()
        tenant_audit(scope,actor_user_id,'storage_location_create','storage_location',str(lid),name)
        return True,'Место хранения создано.',lid
    except Exception:
        return False,'Такое место хранения уже существует.',None

def owner_company_summaries(limit:int=100)->list[dict]:
    q="""SELECT a.id,a.name,a.scope_chat_id,a.owner_user_id,a.owner_chat_id,a.created_at,
       (SELECT COUNT(*) FROM operations o WHERE o.chat_id=a.scope_chat_id) operations,
       (SELECT COUNT(*) FROM inventory i WHERE i.chat_id=a.scope_chat_id) inventory_rows,
       (SELECT COUNT(*) FROM departments d WHERE d.chat_id=a.scope_chat_id AND d.is_archived=0) departments,
       (SELECT COUNT(*) FROM account_user_access ua WHERE ua.account_id=a.id) users
       FROM accounting_accounts a WHERE a.is_archived=0 ORDER BY a.created_at DESC LIMIT ?"""
    return [dict(r) for r in db.fetchall(q,(int(limit),))]

def owner_company_report(account_id:int)->str:
    a=get_account_by_id(account_id)
    if not a: return 'Организация не найдена.'
    q="""SELECT (SELECT COUNT(*) FROM operations WHERE chat_id=?) operations,
       (SELECT COUNT(*) FROM inventory WHERE chat_id=?) inventory_rows,
       (SELECT COUNT(*) FROM departments WHERE chat_id=? AND is_archived=0) departments,
       (SELECT COUNT(*) FROM areas WHERE chat_id=? AND is_archived=0) areas,
       (SELECT MAX(created_at) FROM operations WHERE chat_id=?) last_operation"""
    r=db.fetchone(q,(a.scope_chat_id,)*5); d=dict(r or {})
    return (f'Организация: {a.name}\nID учёта: {a.id}\nВладелец Telegram: {a.owner_user_id}\n'
            f'Участков: {d.get("areas",0)}\nОтделов: {d.get("departments",0)}\nСтрок склада: {d.get("inventory_rows",0)}\n'
            f'Операций: {d.get("operations",0)}\nПоследняя операция: {d.get("last_operation") or "нет данных"}')

# --- Step 82 physical stock view ------------------------------------------
def stock_location_breakdown(chat_id:int, entity_type:str|None=None, entity_id:int|None=None)->list[dict]:
    scope=resolve_scope_chat_id(chat_id)
    where=['i.chat_id=?','e.is_archived=0']; params:list[object]=[scope]
    if entity_type:
        where.append('i.entity_type=?'); params.append(entity_type)
    if entity_id is not None:
        where.append('i.entity_id=?'); params.append(int(entity_id))
    rows=db.fetchall(f"""SELECT i.area_id,a.name area_name,a.site_id,s.name site_name,s.settlement,
        i.entity_type,i.entity_id,e.name entity_name,i.unit,i.quantity,
        COALESCE((SELECT SUM(x.quantity) FROM inventory_allocations x WHERE x.chat_id=i.chat_id
          AND ((x.area_id IS NULL AND i.area_id IS NULL) OR x.area_id=i.area_id)
          AND x.entity_type=i.entity_type AND x.entity_id=i.entity_id AND x.unit=i.unit),0) allocated
        FROM inventory i JOIN entities e ON e.id=i.entity_id AND e.chat_id=i.chat_id
        LEFT JOIN areas a ON a.id=i.area_id LEFT JOIN company_sites s ON s.id=a.site_id
        WHERE {' AND '.join(where)} ORDER BY COALESCE(s.settlement,''),COALESCE(s.name,''),COALESCE(a.name,''),e.name""",tuple(params))
    out=[]
    for r in rows:
        d=dict(r)
        d['unallocated']=float(d.get('quantity') or 0)-float(d.get('allocated') or 0)
        alloc=db.fetchall("""SELECT x.*,d.name department_name,l.name location_name,l.code location_code
            FROM inventory_allocations x LEFT JOIN departments d ON d.id=x.department_id LEFT JOIN storage_locations l ON l.id=x.location_id
            WHERE x.chat_id=? AND ((x.area_id IS NULL AND ? IS NULL) OR x.area_id=?) AND x.entity_type=? AND x.entity_id=? AND x.unit=? AND ABS(x.quantity)>0.0000001
            ORDER BY COALESCE(d.name,''),COALESCE(l.name,'')""",(scope,d.get('area_id'),d.get('area_id'),d['entity_type'],d['entity_id'],d['unit']))
        d['allocations']=[dict(x) for x in alloc]
        out.append(d)
    return out


def allocation_quantity(chat_id:int,entity_type:str,entity_id:int,unit:str='шт',area_id:int|None=None,department_id:int|None=None,location_id:int|None=None)->float:
    scope=resolve_scope_chat_id(chat_id)
    where=['chat_id=?','entity_type=?','entity_id=?','unit=?']; params:list[object]=[scope,entity_type,int(entity_id),unit]
    for col,val in [('area_id',area_id),('department_id',department_id),('location_id',location_id)]:
        if val is not None:
            where.append(f'{col}=?'); params.append(int(val))
    r=db.fetchone(f"SELECT COALESCE(SUM(quantity),0) q FROM inventory_allocations WHERE {' AND '.join(where)}",tuple(params))
    return float(r['q'] if r else 0)
