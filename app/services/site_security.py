from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_telegram_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86400) -> dict:
    """Validate Telegram Web App init data and return parsed user data.

    Empty input simply returns an empty dict. The caller decides whether a
    fallback access token is allowed.
    """
    raw = (init_data or "").strip()
    if not raw or not bot_token:
        return {}
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return {}
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return {}
    try:
        auth_date = int(pairs.get("auth_date", "0") or 0)
    except ValueError:
        auth_date = 0
    if auth_date and time.time() - auth_date > max_age_seconds:
        return {}
    user_raw = pairs.get("user") or "{}"
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        user = {}
    return user if isinstance(user, dict) else {}
