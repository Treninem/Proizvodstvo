from __future__ import annotations

from .normalize import normalize_key


# Команды резервного копирования распознаются только целиком. Отдельные слова
# «резерв», «копия», «база» и похожие выражения часто встречаются в переписке.
_ACCOUNT_BACKUP_COMMANDS = {
    "backup",
    "backup учета",
    "backup учёта",
    "бэкап",
    "бэкап учета",
    "бэкап учёта",
    "копия учета",
    "копия учёта",
    "копия текущего учета",
    "копия текущего учёта",
    "резервная копия",
    "резервная копия учета",
    "резервная копия учёта",
    "сделать копию учета",
    "сделать копию учёта",
    "создать копию учета",
    "создать копию учёта",
    "скачать копию учета",
    "скачать копию учёта",
}

_FULL_BACKUP_COMMANDS = {
    "backup full",
    "full backup",
    "полная копия базы",
    "полная копия",
    "полная резервная копия",
    "полный бэкап",
    "полный backup",
    "копия всей базы",
}

_BACKUP_LIST_COMMANDS = {
    "backups",
    "backup list",
    "список копий",
    "последние копии",
    "история копий",
    "список резервных копий",
    "последние резервные копии",
}

_ACCOUNT_BACKUP_KEYS = frozenset(normalize_key(value) for value in _ACCOUNT_BACKUP_COMMANDS)
_FULL_BACKUP_KEYS = frozenset(normalize_key(value) for value in _FULL_BACKUP_COMMANDS)
_BACKUP_LIST_KEYS = frozenset(normalize_key(value) for value in _BACKUP_LIST_COMMANDS)


def backup_request_kind(text: str) -> str | None:
    """Возвращает account/full/list только для явной полной команды."""
    key = normalize_key(text)
    if not key:
        return None
    if key in _FULL_BACKUP_KEYS:
        return "full"
    if key in _BACKUP_LIST_KEYS:
        return "list"
    if key in _ACCOUNT_BACKUP_KEYS:
        return "account"
    return None
