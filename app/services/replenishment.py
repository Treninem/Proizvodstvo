from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from .. import db
from . import repository as repo
from . import stock_risk

OPEN_STATUSES = {"requested", "approved", "ordered", "partial"}
ACTIONS = {"approve", "order", "receive", "reject", "cancel"}


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(int(chat_id))


def _entity(scope: int, entity_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM entities WHERE chat_id=? AND id=? AND is_archived=0", (scope, int(entity_id)))
    return dict(row) if row else None


def _visible(scope: int, user_id: int, entity_id: int) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    ids = repo.visible_entity_ids_for_user(scope, user_id)
    return ids is None or int(entity_id) in {int(x) for x in ids}


def _can_manage(scope: int, user_id: int, entity_id: int | None = None) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    if not repo.user_can_manage_departments(scope, user_id):
        return False
    return entity_id is None or _visible(scope, user_id, int(entity_id))


def _inventory(scope: int, entity_id: int, area_id: int | None) -> float:
    if area_id:
        row = db.fetchone("SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=? AND area_id=?", (scope, entity_id, area_id))
    else:
        row = db.fetchone("SELECT COALESCE(SUM(quantity),0) AS quantity FROM inventory WHERE chat_id=? AND entity_id=?", (scope, entity_id))
    return float(row["quantity"] or 0) if row else 0.0


def save_setting(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = _scope(chat_id)
    entity_id = int(values.get("entity_id") or 0)
    entity = _entity(scope, entity_id)
    if not entity:
        raise ValueError("Позиция не найдена.")
    if not _can_manage(scope, actor_user_id, entity_id):
        raise PermissionError("Нет права на настройку пополнения этой позиции.")
    area_id = int(values["area_id"]) if values.get("area_id") else None
    if area_id and not db.fetchone("SELECT id FROM areas WHERE chat_id=? AND id=?", (scope, area_id)):
        raise ValueError("Площадка не найдена.")
    params = (
        scope, entity_id, area_id,
        max(0.0, float(values.get("lead_time_days") or 0)),
        max(0.0, float(values.get("target_cover_shifts") or 10)),
        max(0.0, float(values.get("minimum_order_quantity") or 0)),
        max(0.0, float(values.get("pack_quantity") or 0)),
        str(values.get("preferred_supplier") or "")[:200],
        int(bool(values.get("is_enabled", True))), int(actor_user_id),
    )
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM replenishment_settings WHERE chat_id=? AND entity_id=? AND COALESCE(area_id,0)=COALESCE(?,0)", (scope, entity_id, area_id)).fetchone()
        if row:
            sid = int(row["id"])
            conn.execute("""UPDATE replenishment_settings SET lead_time_days=?,target_cover_shifts=?,minimum_order_quantity=?,pack_quantity=?,preferred_supplier=?,is_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""", params[3:9] + (sid,))
        else:
            cur = conn.execute("""INSERT INTO replenishment_settings(chat_id,entity_id,area_id,lead_time_days,target_cover_shifts,minimum_order_quantity,pack_quantity,preferred_supplier,is_enabled,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)""", params)
            sid = int(cur.lastrowid)
        conn.commit()
    return dict(db.fetchone("SELECT * FROM replenishment_settings WHERE id=?", (sid,)))


def list_settings(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall("""SELECT s.*,e.name AS entity_name,e.default_unit,a.name AS area_name FROM replenishment_settings s JOIN entities e ON e.id=s.entity_id LEFT JOIN areas a ON a.id=s.area_id WHERE s.chat_id=? ORDER BY e.name,a.name""", (scope,))
    return [dict(r) for r in rows if _visible(scope, user_id, int(r["entity_id"]))]


def _setting_for(scope: int, entity_id: int, area_id: int | None) -> dict[str, Any] | None:
    row = db.fetchone("""SELECT * FROM replenishment_settings WHERE chat_id=? AND entity_id=? AND is_enabled=1 AND (area_id=? OR area_id IS NULL) ORDER BY CASE WHEN area_id=? THEN 0 ELSE 1 END,id DESC LIMIT 1""", (scope, entity_id, area_id, area_id))
    return dict(row) if row else None


def _round_order(qty: float, minimum: float, pack: float) -> float:
    qty = max(qty, minimum, 0.0)
    if pack > 0:
        qty = math.ceil(qty / pack - 1e-12) * pack
    return qty


def forecast(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    result: list[dict[str, Any]] = []
    for rule in stock_risk.list_rules(scope, include_disabled=False):
        entity_id = int(rule["entity_id"])
        if not _visible(scope, user_id, entity_id):
            continue
        snap = stock_risk.evaluate_rule(int(rule["id"]))
        if not snap:
            continue
        setting = _setting_for(scope, entity_id, int(rule["area_id"]) if rule.get("area_id") else None)
        if not setting and snap.severity not in {"warning", "critical", "emergency"}:
            continue
        lead_days = float((setting or {}).get("lead_time_days") or 0)
        shifts_per_day = float(rule.get("shifts_per_day") or 1)
        target_cover = float((setting or {}).get("target_cover_shifts") or max(float(rule.get("warning_shifts") or 0), 5))
        target = snap.consumption_per_shift * (target_cover + lead_days * shifts_per_day)
        need = max(0.0, target - snap.effective_stock)
        need = _round_order(need, float((setting or {}).get("minimum_order_quantity") or 0), float((setting or {}).get("pack_quantity") or 0))
        open_row = db.fetchone("""SELECT COALESCE(SUM(requested_quantity),0) AS qty FROM replenishment_requests WHERE chat_id=? AND entity_id=? AND COALESCE(area_id,0)=COALESCE(?,0) AND status IN ('requested','approved','ordered','partial')""", (scope, entity_id, rule.get("area_id")))
        open_qty = float(open_row["qty"] or 0) if open_row else 0.0
        recommended = max(0.0, need - open_qty)
        needed_at = None
        if snap.reserve_shifts is not None and snap.consumption_per_shift > 0:
            days = max(0.0, snap.reserve_shifts / max(0.01, shifts_per_day) - lead_days)
            needed_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        result.append({
            "rule_id": int(rule["id"]), "entity_id": entity_id, "entity_name": rule.get("entity_name"), "unit": rule.get("default_unit") or "шт",
            "area_id": rule.get("area_id"), "area_name": rule.get("area_name") or "", "severity": snap.severity,
            "available_quantity": snap.effective_stock, "stock_quantity": snap.stock_quantity, "consumption_per_shift": snap.consumption_per_shift,
            "reserve_shifts": snap.reserve_shifts, "target_cover_shifts": target_cover, "lead_time_days": lead_days,
            "recommended_quantity": recommended, "open_request_quantity": open_qty, "needed_at": needed_at,
            "preferred_supplier": (setting or {}).get("preferred_supplier") or "", "reason": snap.message,
            "can_manage": _can_manage(scope, user_id, entity_id),
        })
    result.sort(key=lambda x: ({"emergency":0,"critical":1,"warning":2,"ok":3,"unknown":4}.get(str(x["severity"]),5), str(x["entity_name"])))
    return result


def create_request(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = _scope(chat_id)
    entity_id = int(values.get("entity_id") or 0)
    entity = _entity(scope, entity_id)
    if not entity or not _visible(scope, actor_user_id, entity_id):
        raise PermissionError("Позиция недоступна.")
    qty = float(values.get("requested_quantity") or 0)
    if qty <= 0:
        raise ValueError("Количество должно быть больше нуля.")
    area_id = int(values["area_id"]) if values.get("area_id") else None
    if area_id is not None and not db.fetchone("SELECT id FROM areas WHERE chat_id=? AND id=? AND is_archived=0", (scope, area_id)):
        raise ValueError("Площадка не найдена в этой организации.")
    rule_id = int(values["source_rule_id"]) if values.get("source_rule_id") else None
    if rule_id:
        source_rule = db.fetchone(
            "SELECT id,entity_id,area_id FROM stock_alert_rules WHERE id=? AND chat_id=? AND is_enabled=1",
            (rule_id, scope),
        )
        if not source_rule:
            raise ValueError("Правило пополнения не найдено в этой организации.")
        if int(source_rule["entity_id"]) != entity_id:
            raise ValueError("Правило пополнения относится к другой позиции.")
        source_area = int(source_rule["area_id"]) if source_rule["area_id"] is not None else None
        if area_id is not None and source_area is not None and source_area != area_id:
            raise ValueError("Правило пополнения относится к другой площадке.")
        if area_id is None and source_area is not None:
            area_id = source_area
    snap = stock_risk.evaluate_rule(rule_id) if rule_id else None
    available = snap.effective_stock if snap else _inventory(scope, entity_id, area_id)
    consumption = snap.consumption_per_shift if snap else 0.0
    reserve = snap.reserve_shifts if snap else None
    with db.connect() as conn:
        cur = conn.execute("""INSERT INTO replenishment_requests(chat_id,entity_id,area_id,requested_by,requested_quantity,unit,status,source,source_rule_id,available_quantity,consumption_per_shift,reserve_shifts,recommended_quantity,lead_time_days,needed_at,supplier_note,reason,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scope,entity_id,area_id,int(actor_user_id),qty,str(values.get("unit") or entity.get("default_unit") or "шт")[:30],"requested",str(values.get("source") or "manual")[:30],rule_id,available,consumption,reserve,float(values.get("recommended_quantity") or qty),float(values.get("lead_time_days") or 0),str(values.get("needed_at") or "")[:30] or None,str(values.get("supplier_note") or "")[:500],str(values.get("reason") or "")[:1000],str(values.get("note") or "")[:1000]))
        rid=int(cur.lastrowid)
        conn.execute("INSERT INTO replenishment_request_events(request_id,actor_user_id,action,quantity,reason,note) VALUES(?,?,?,?,?,?)", (rid,int(actor_user_id),"created",qty,str(values.get("reason") or "")[:1000],""))
        conn.commit()
    for uid in repo.tenant_admin_user_ids(scope):
        if int(uid)!=int(actor_user_id):
            repo.create_inbox_item(scope,int(uid),"replenishment",f"Заявка на пополнение №{rid}",f"{entity['name']}: {qty:g} {entity.get('default_unit') or 'шт'}","replenishment_request",rid,deduplicate=False,priority="high")
    return get_request(scope, rid, actor_user_id) or {"id":rid}


def get_request(chat_id:int, request_id:int, user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id)
    row=db.fetchone("""SELECT r.*,e.name AS entity_name,a.name AS area_name FROM replenishment_requests r JOIN entities e ON e.id=r.entity_id AND e.chat_id=r.chat_id LEFT JOIN areas a ON a.id=r.area_id AND a.chat_id=r.chat_id WHERE r.chat_id=? AND r.id=?""",(scope,int(request_id)))
    if not row or not _visible(scope,user_id,int(row["entity_id"])): return None
    item=dict(row); item["can_manage"]=_can_manage(scope,user_id,int(item["entity_id"])); item["events"]=[dict(x) for x in db.fetchall("SELECT * FROM replenishment_request_events WHERE request_id=? ORDER BY id DESC",(int(request_id),))]
    return item


def list_requests(chat_id:int,user_id:int,limit:int=200)->list[dict[str,Any]]:
    scope=_scope(chat_id)
    rows=db.fetchall("""SELECT r.*,e.name AS entity_name,a.name AS area_name FROM replenishment_requests r JOIN entities e ON e.id=r.entity_id AND e.chat_id=r.chat_id LEFT JOIN areas a ON a.id=r.area_id AND a.chat_id=r.chat_id WHERE r.chat_id=? ORDER BY CASE r.status WHEN 'requested' THEN 0 WHEN 'approved' THEN 1 WHEN 'ordered' THEN 2 WHEN 'partial' THEN 3 ELSE 4 END,r.id DESC LIMIT ?""",(scope,max(1,min(int(limit),500))))
    out=[]
    for r in rows:
        if _visible(scope,user_id,int(r["entity_id"])):
            d=dict(r); d["can_manage"]=_can_manage(scope,user_id,int(r["entity_id"])); out.append(d)
    return out


def request_action(chat_id:int,actor_user_id:int,request_id:int,action:str,*,quantity:float|None=None,reason:str="",note:str="")->dict[str,Any]:
    scope=_scope(chat_id); item=get_request(scope,request_id,actor_user_id)
    if not item: raise ValueError("Заявка не найдена.")
    if action not in ACTIONS: raise ValueError("Неизвестное действие.")
    if not _can_manage(scope,actor_user_id,int(item["entity_id"])): raise PermissionError("Нет права менять эту заявку.")
    status=str(item["status"])
    transitions={"approve":({"requested"},"approved"),"order":({"approved","requested"},"ordered"),"receive":({"ordered","approved","partial"},"received"),"reject":({"requested","approved"},"rejected"),"cancel":(OPEN_STATUSES,"cancelled")}
    allowed,target=transitions[action]
    if status not in allowed: raise ValueError("Текущий статус заявки не позволяет это действие.")
    if action in {"reject","cancel"} and not str(reason).strip(): raise ValueError("Укажите причину.")
    q=float(quantity if quantity is not None else item["requested_quantity"])
    with db.connect() as conn:
        fields={"approve":"approved_by=?,approved_at=CURRENT_TIMESTAMP","order":"ordered_by=?,ordered_at=CURRENT_TIMESTAMP","receive":"received_by=?,received_at=CURRENT_TIMESTAMP,closed_at=CURRENT_TIMESTAMP","reject":"closed_at=CURRENT_TIMESTAMP","cancel":"closed_at=CURRENT_TIMESTAMP"}
        params=[]
        if action in {"approve","order","receive"}: params.append(int(actor_user_id))
        params += [target,str(reason or item.get("reason") or "")[:1000],int(request_id),scope]
        conn.execute(f"UPDATE replenishment_requests SET {fields[action]},status=?,reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND chat_id=?",tuple(params))
        conn.execute("INSERT INTO replenishment_request_events(request_id,actor_user_id,action,quantity,reason,note) VALUES(?,?,?,?,?,?)",(int(request_id),int(actor_user_id),action,q,str(reason)[:1000],str(note)[:1000]))
        conn.commit()
    # Receipt deliberately does not change inventory: warehouse receipt remains an explicit stock operation.
    return get_request(scope,request_id,actor_user_id) or item


def snapshot(chat_id:int,user_id:int)->dict[str,Any]:
    suggestions=forecast(chat_id,user_id); requests=list_requests(chat_id,user_id)
    return {"suggestions":suggestions,"requests":requests,"settings":list_settings(chat_id,user_id),"counts":{"critical":sum(1 for x in suggestions if x["severity"] in {"critical","emergency"}),"suggested":sum(1 for x in suggestions if float(x["recommended_quantity"] or 0)>0),"open_requests":sum(1 for x in requests if x["status"] in OPEN_STATUSES)}}


def queue_forecast_notifications(now:datetime|None=None)->int:
    """Creates at most one daily notification for critical forecast without enough open request."""
    now=now or datetime.now();day=now.strftime("%Y-%m-%d");created=0
    scopes={int(r["chat_id"]) for r in db.fetchall("SELECT DISTINCT chat_id FROM stock_alert_rules WHERE is_enabled=1")}
    for scope in scopes:
        for item in forecast(scope, repo.tenant_admin_user_ids(scope)[0] if repo.tenant_admin_user_ids(scope) else 0):
            if item["severity"] not in {"critical","emergency"} or float(item["recommended_quantity"] or 0)<=0:continue
            for uid in repo.tenant_admin_user_ids(scope):
                key=f"replenishment:{int(item['rule_id'])}:{day}"
                try:
                    with db.connect() as conn:
                        cur=conn.execute("INSERT OR IGNORE INTO workflow_notifications(chat_id,object_type,object_id,notification_key,recipient_user_id) VALUES(?,?,?,?,?)",(scope,"replenishment",int(item["rule_id"]),key,int(uid)))
                        if int(cur.rowcount or 0)<=0:continue
                        conn.commit()
                    repo.create_inbox_item(scope,int(uid),"replenishment_critical",f"Нужно пополнение: {item['entity_name']}",f"Рекомендуется {float(item['recommended_quantity']):g} {item['unit']}. Запас: {item['reserve_shifts'] if item['reserve_shifts'] is not None else 'неизвестно'} смен.","stock_alert_rule",int(item["rule_id"]),deduplicate=False,priority="urgent")
                    created+=1
                except Exception:
                    pass
    return created
