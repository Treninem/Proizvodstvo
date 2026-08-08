from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import db


STEP_PRODUCTION_LINK = "production_link"
STEP_RISK_OBSERVATION = "risk_observation"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def queue_operation_steps(
    conn,
    chat_id: int,
    operation_id: int,
    user_id: int,
    op: dict[str, Any],
    raw_text: str,
) -> None:
    """Добавляет восстановимые пост-операционные шаги в ту же транзакцию, что и сама операция."""
    payload = {
        "chat_id": int(chat_id),
        "operation_id": int(operation_id),
        "user_id": int(user_id),
        "op": dict(op),
        "raw_text": str(raw_text or "")[:4000],
    }
    encoded = _safe_payload(payload)
    for step_key in (STEP_PRODUCTION_LINK, STEP_RISK_OBSERVATION):
        conn.execute(
            """
            INSERT OR IGNORE INTO reliability_journal(
                chat_id,object_type,object_id,step_key,payload_json,status
            ) VALUES(?,?,?,?,?,'pending')
            """,
            (int(chat_id), "operation", int(operation_id), step_key, encoded),
        )


def _mark_done(row_id: int) -> None:
    db.execute(
        "UPDATE reliability_journal SET status='done',last_error='',next_retry_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(row_id),),
    )


def _mark_error(row_id: int, attempts: int, exc: Exception) -> None:
    attempts = max(1, int(attempts))
    delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
    next_retry = (datetime.now() + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """
        UPDATE reliability_journal
        SET status='error',attempts=?,last_error=?,next_retry_at=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (attempts, str(exc)[:1000], next_retry, int(row_id)),
    )


def _execute_step(step_key: str, payload: dict[str, Any]) -> None:
    chat_id = int(payload.get("chat_id") or 0)
    operation_id = int(payload.get("operation_id") or 0)
    user_id = int(payload.get("user_id") or 0)
    op = dict(payload.get("op") or {})
    raw_text = str(payload.get("raw_text") or "")
    if not chat_id or not operation_id or not user_id:
        raise ValueError("В журнале восстановления отсутствуют обязательные идентификаторы.")
    if step_key == STEP_PRODUCTION_LINK:
        from . import production_flow
        production_flow.attach_operation(chat_id, user_id, operation_id, op)
        return
    if step_key == STEP_RISK_OBSERVATION:
        if op.get("skip_risk_observation"):
            return
        from . import stock_risk
        source = str(op.get("source_channel") or ("mini" if raw_text.lower().startswith("mini app") else "bot"))
        stock_risk.record_operation_observation(chat_id, user_id, op, operation_id, raw_text, source=source)
        return
    raise ValueError(f"Неизвестный шаг восстановления: {step_key}")


def process_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    result = {"done": 0, "error": 0}
    for item in rows:
        row_id = int(item["id"])
        attempts = int(item.get("attempts") or 0) + 1
        try:
            payload = json.loads(str(item.get("payload_json") or "{}"))
            _execute_step(str(item.get("step_key") or ""), payload)
            _mark_done(row_id)
            result["done"] += 1
        except Exception as exc:
            _mark_error(row_id, attempts, exc)
            result["error"] += 1
    return result


def process_for_operation(operation_id: int) -> dict[str, int]:
    rows = [dict(r) for r in db.fetchall(
        """
        SELECT * FROM reliability_journal
        WHERE object_type='operation' AND object_id=? AND status IN ('pending','error')
        ORDER BY id
        """,
        (int(operation_id),),
    )]
    return process_rows(rows)


def process_pending(limit: int = 100) -> dict[str, int]:
    rows = [dict(r) for r in db.fetchall(
        """
        SELECT * FROM reliability_journal
        WHERE status IN ('pending','error')
          AND (next_retry_at IS NULL OR next_retry_at<=CURRENT_TIMESTAMP)
        ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,id
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    )]
    return process_rows(rows)


def journal_health(chat_id: int | None = None) -> dict[str, int]:
    params: tuple[Any, ...] = ()
    where = ""
    if chat_id is not None:
        where = "WHERE chat_id=?"
        params = (int(chat_id),)
    rows = db.fetchall(
        f"SELECT status,COUNT(*) AS n FROM reliability_journal {where} GROUP BY status",
        params,
    )
    result = {"pending": 0, "error": 0, "done": 0}
    for row in rows:
        result[str(row["status"])] = int(row["n"] or 0)
    return result
