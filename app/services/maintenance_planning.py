from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db
from . import repository as repo
from . import accounting


def _scope(chat_id:int)->int: return repo.resolve_scope_chat_id(int(chat_id))

def _eq(scope:int,equipment_id:int)->dict[str,Any]|None:
    row=db.fetchone("SELECT eq.*,d.name AS department_name,a.name AS area_name FROM equipment eq LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE eq.chat_id=? AND eq.id=? AND eq.is_archived=0",(scope,int(equipment_id)))
    return dict(row) if row else None

def _level(dep:int|None,user:int)->int: return int(repo.department_actor_level(int(dep),int(user)) or 0) if dep else 0

def _can_view(scope:int,user:int,eq:dict[str,Any])->bool: return repo.is_system_admin_id(user) or bool(eq.get("department_id") and _level(eq.get("department_id"),user)>=10)

def _can_manage(scope:int,user:int,eq:dict[str,Any])->bool: return repo.is_system_admin_id(user) or bool(eq.get("department_id") and _level(eq.get("department_id"),user)>=50)

def _parse(value:str|None)->datetime|None:
    if not value:return None
    try:return datetime.fromisoformat(str(value).replace("Z","+00:00").replace("+00:00",""))
    except Exception:return None

def _notify(scope:int,uid:int|None,title:str,message:str,oid:int,priority:str="high")->None:
    if uid:
        repo.create_inbox_item(scope,int(uid),"maintenance",title,message,"maintenance_work_order",int(oid),deduplicate=False,priority=priority)

def save_plan(chat_id:int,actor_user_id:int,values:dict[str,Any])->dict[str,Any]:
    scope=_scope(chat_id); equipment_id=int(values.get("equipment_id") or 0); eq=_eq(scope,equipment_id)
    if not eq: raise ValueError("Оборудование не найдено.")
    if not _can_manage(scope,actor_user_id,eq): raise PermissionError("Нет права настраивать ТО этого оборудования.")
    interval=max(0,int(values.get("interval_days") or eq.get("service_interval_days") or 0)); warning=max(0,int(values.get("warning_before_days") or eq.get("warning_before_days") or 3))
    responsible=int(values["responsible_user_id"]) if values.get("responsible_user_id") else None
    if responsible and eq.get("department_id") and _level(int(eq["department_id"]),responsible)<10 and not repo.is_system_admin_id(responsible): raise ValueError("Ответственный не имеет доступа к отделу оборудования.")
    next_due=str(values.get("next_due_at") or eq.get("next_service_at") or "")[:30] or None
    if not next_due and interval: next_due=(datetime.now()+timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S")
    checklist=list(values.get("checklist") or []); parts=list(values.get("spare_parts") or [])
    with db.connect() as conn:
        row=conn.execute("SELECT id FROM maintenance_plans WHERE chat_id=? AND equipment_id=?",(scope,equipment_id)).fetchone()
        if row:
            pid=int(row["id"]); conn.execute("UPDATE maintenance_plans SET responsible_user_id=?,interval_days=?,warning_before_days=?,next_due_at=?,is_enabled=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(responsible,interval,warning,next_due,int(bool(values.get("is_enabled",True))),str(values.get("note") or "")[:1000],pid)); conn.execute("DELETE FROM maintenance_checklist_items WHERE plan_id=?",(pid,));conn.execute("DELETE FROM maintenance_spare_parts WHERE plan_id=?",(pid,))
        else:
            cur=conn.execute("INSERT INTO maintenance_plans(chat_id,equipment_id,responsible_user_id,interval_days,warning_before_days,next_due_at,is_enabled,note,created_by) VALUES(?,?,?,?,?,?,?,?,?)",(scope,equipment_id,responsible,interval,warning,next_due,int(bool(values.get("is_enabled",True))),str(values.get("note") or "")[:1000],int(actor_user_id)));pid=int(cur.lastrowid)
        for i,item in enumerate(checklist):
            label=str(item.get("label") if isinstance(item,dict) else item).strip()
            if label: conn.execute("INSERT INTO maintenance_checklist_items(plan_id,label,is_required,sort_order) VALUES(?,?,?,?)",(pid,label[:300],int(bool(item.get("is_required",True))) if isinstance(item,dict) else 1,i))
        for item in parts:
            if not isinstance(item,dict): continue
            eid=int(item.get("entity_id") or 0); qty=max(0,float(item.get("planned_quantity") or 0)); area=int(item["area_id"]) if item.get("area_id") else None
            ent=conn.execute("SELECT default_unit FROM entities WHERE chat_id=? AND id=? AND is_archived=0",(scope,eid)).fetchone()
            if eid and ent and qty>0: conn.execute("INSERT INTO maintenance_spare_parts(plan_id,entity_id,area_id,planned_quantity,unit) VALUES(?,?,?,?,?)",(pid,eid,area,qty,str(item.get("unit") or ent["default_unit"] or "шт")[:30]))
        conn.execute("UPDATE equipment SET service_interval_days=?,warning_before_days=?,next_service_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(interval,warning,next_due,equipment_id));conn.commit()
    ensure_work_orders(scope,now=datetime.now())
    return get_plan(scope,pid,actor_user_id) or {"id":pid}

def get_plan(chat_id:int,plan_id:int,user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id); row=db.fetchone("SELECT p.*,eq.name AS equipment_name,eq.department_id,eq.area_id,d.name AS department_name,a.name AS area_name FROM maintenance_plans p JOIN equipment eq ON eq.id=p.equipment_id LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE p.chat_id=? AND p.id=?",(scope,int(plan_id)))
    if not row:return None
    item=dict(row)
    if not _can_view(scope,user_id,item):return None
    item["can_manage"]=_can_manage(scope,user_id,item); item["checklist"]=[dict(r) for r in db.fetchall("SELECT * FROM maintenance_checklist_items WHERE plan_id=? ORDER BY sort_order,id",(int(plan_id),))];item["spare_parts"]=[dict(r) for r in db.fetchall("SELECT ms.*,e.name AS entity_name,a.name AS area_name FROM maintenance_spare_parts ms JOIN entities e ON e.id=ms.entity_id LEFT JOIN areas a ON a.id=ms.area_id WHERE ms.plan_id=? ORDER BY ms.id",(int(plan_id),))]
    return item

def list_plans(chat_id:int,user_id:int)->list[dict[str,Any]]:
    scope=_scope(chat_id); rows=db.fetchall("SELECT p.*,eq.name AS equipment_name,eq.department_id,eq.area_id,d.name AS department_name,a.name AS area_name FROM maintenance_plans p JOIN equipment eq ON eq.id=p.equipment_id LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE p.chat_id=? ORDER BY COALESCE(p.next_due_at,'9999'),eq.name",(scope,));out=[]
    for r in rows:
        d=dict(r)
        if _can_view(scope,user_id,d):d["can_manage"]=_can_manage(scope,user_id,d);out.append(d)
    return out

def ensure_work_orders(chat_id:int,now:datetime|None=None)->int:
    scope=_scope(chat_id); now=now or datetime.now();created=0
    rows=db.fetchall("SELECT p.*,eq.name AS equipment_name FROM maintenance_plans p JOIN equipment eq ON eq.id=p.equipment_id WHERE p.chat_id=? AND p.is_enabled=1 AND p.next_due_at IS NOT NULL",(scope,))
    for r in rows:
        p=dict(r);due=_parse(p.get("next_due_at"));
        if not due or due>now+timedelta(days=max(0,int(p.get("warning_before_days") or 0))):continue
        with db.connect() as conn:
            exists=conn.execute("SELECT id FROM maintenance_work_orders WHERE plan_id=? AND due_at=?",(int(p["id"]),str(p["next_due_at"]))).fetchone()
            if exists:continue
            cur=conn.execute("INSERT INTO maintenance_work_orders(chat_id,plan_id,equipment_id,responsible_user_id,due_at,status) VALUES(?,?,?,?,?,'planned')",(scope,int(p["id"]),int(p["equipment_id"]),p.get("responsible_user_id"),str(p["next_due_at"])));wid=int(cur.lastrowid)
            for c in conn.execute("SELECT * FROM maintenance_checklist_items WHERE plan_id=? ORDER BY sort_order,id",(int(p["id"]),)).fetchall():conn.execute("INSERT INTO maintenance_work_checks(work_order_id,checklist_item_id,label,is_required,sort_order) VALUES(?,?,?,?,?)",(wid,int(c["id"]),str(c["label"]),int(c["is_required"]),int(c["sort_order"])))
            for s in conn.execute("SELECT * FROM maintenance_spare_parts WHERE plan_id=?",(int(p["id"]),)).fetchall():conn.execute("INSERT INTO maintenance_work_parts(work_order_id,entity_id,area_id,planned_quantity,unit) VALUES(?,?,?,?,?)",(wid,int(s["entity_id"]),s["area_id"],float(s["planned_quantity"]),str(s["unit"])))
            conn.commit();created+=1
        _notify(scope,p.get("responsible_user_id"),f"ТО: {p['equipment_name']}",f"Срок обслуживания: {p['next_due_at']}",wid,"urgent" if due<=now else "high")
    return created

def _work_row(scope:int,wid:int)->dict[str,Any]|None:
    row=db.fetchone("SELECT w.*,eq.name AS equipment_name,eq.department_id,eq.area_id,d.name AS department_name,a.name AS area_name FROM maintenance_work_orders w JOIN equipment eq ON eq.id=w.equipment_id LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE w.chat_id=? AND w.id=?",(scope,int(wid)));return dict(row) if row else None

def get_work_order(chat_id:int,wid:int,user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id);item=_work_row(scope,wid)
    if not item or not _can_view(scope,user_id,item):return None
    item["can_manage"]=_can_manage(scope,user_id,item) or int(item.get("responsible_user_id") or 0)==int(user_id);item["checks"]=[dict(r) for r in db.fetchall("SELECT * FROM maintenance_work_checks WHERE work_order_id=? ORDER BY sort_order,id",(int(wid),))];item["parts"]=[dict(r) for r in db.fetchall("SELECT mp.*,e.name AS entity_name,a.name AS area_name FROM maintenance_work_parts mp JOIN entities e ON e.id=mp.entity_id LEFT JOIN areas a ON a.id=mp.area_id WHERE mp.work_order_id=? ORDER BY mp.id",(int(wid),))];return item

def list_work_orders(chat_id:int,user_id:int,limit:int=200)->list[dict[str,Any]]:
    scope=_scope(chat_id);rows=db.fetchall("SELECT w.*,eq.name AS equipment_name,eq.department_id,d.name AS department_name FROM maintenance_work_orders w JOIN equipment eq ON eq.id=w.equipment_id LEFT JOIN departments d ON d.id=eq.department_id WHERE w.chat_id=? ORDER BY CASE w.status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,w.due_at LIMIT ?",(scope,max(1,min(int(limit),500))));out=[]
    for r in rows:
        d=dict(r)
        if _can_view(scope,user_id,d):d["can_manage"]=_can_manage(scope,user_id,d) or int(d.get("responsible_user_id") or 0)==int(user_id);out.append(d)
    return out

def set_check(chat_id:int,actor_user_id:int,wid:int,check_id:int,checked:bool,note:str="")->dict[str,Any]:
    scope=_scope(chat_id);work=get_work_order(scope,wid,actor_user_id)
    if not work or not work["can_manage"]:raise PermissionError("Нет права отмечать чек-лист ТО.")
    row=db.fetchone("SELECT id FROM maintenance_work_checks WHERE id=? AND work_order_id=?",(int(check_id),int(wid)))
    if not row:raise ValueError("Пункт чек-листа не найден.")
    db.execute("UPDATE maintenance_work_checks SET is_checked=?,note=?,checked_by=?,checked_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",(int(bool(checked)),str(note)[:500],int(actor_user_id),int(bool(checked)),int(check_id)));return get_work_order(scope,wid,actor_user_id) or work

def set_part(chat_id:int,actor_user_id:int,wid:int,part_id:int,actual_quantity:float)->dict[str,Any]:
    scope=_scope(chat_id);work=get_work_order(scope,wid,actor_user_id)
    if not work or not work["can_manage"]:raise PermissionError("Нет права менять расход запчастей.")
    qty=max(0.0,float(actual_quantity));row=db.fetchone("SELECT * FROM maintenance_work_parts WHERE id=? AND work_order_id=?",(int(part_id),int(wid)))
    if not row:raise ValueError("Запчасть не найдена.")
    if row["operation_id"] is not None:raise ValueError("Расход этой запчасти уже зафиксирован.")
    db.execute("UPDATE maintenance_work_parts SET actual_quantity=? WHERE id=?",(qty,int(part_id)));return get_work_order(scope,wid,actor_user_id) or work

def work_action(chat_id:int,actor_user_id:int,wid:int,action:str,*,result:str="",note:str="")->dict[str,Any]:
    scope=_scope(chat_id);work=get_work_order(scope,wid,actor_user_id)
    if not work:raise ValueError("Задание ТО не найдено.")
    if not work["can_manage"]:raise PermissionError("Нет права управлять этим ТО.")
    status=str(work["status"])
    if action=="start":
        if status!="planned":raise ValueError("ТО уже начато или закрыто.")
        db.execute("UPDATE maintenance_work_orders SET status='in_progress',started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(int(wid),))
    elif action=="cancel":
        if status not in {"planned","in_progress"}:raise ValueError("ТО уже закрыто.")
        if not str(result).strip():raise ValueError("Укажите причину отмены.")
        db.execute("UPDATE maintenance_work_orders SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP,result=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(result)[:1000],int(wid)))
    elif action=="complete":
        if status not in {"planned","in_progress"}:raise ValueError("ТО уже закрыто.")
        missing=db.fetchone("SELECT COUNT(*) AS n FROM maintenance_work_checks WHERE work_order_id=? AND is_required=1 AND is_checked=0",(int(wid),))
        if missing and int(missing["n"] or 0)>0:raise ValueError("Сначала выполните все обязательные пункты чек-листа.")
        if not str(result).strip():raise ValueError("Укажите результат обслуживания.")
        # Deduct spare parts via normal accounting; each row is idempotent after operation_id is saved.
        for p in [dict(x) for x in db.fetchall("SELECT * FROM maintenance_work_parts WHERE work_order_id=?",(int(wid),))]:
            qty=float(p.get("actual_quantity") or 0)
            if qty<=0 or p.get("operation_id"):continue
            ent=db.fetchone("SELECT entity_type FROM entities WHERE id=? AND chat_id=?",(int(p["entity_id"]),scope))
            if not ent:continue
            opid=accounting.record_internal_operation(scope,scope,actor_user_id,{"operation_type":"stock_out","entity_type":str(ent["entity_type"]),"entity_id":int(p["entity_id"]),"quantity":qty,"unit":str(p.get("unit") or "шт"),"area_id":p.get("area_id"),"source_channel":"maintenance"},raw_text=f"ТО №{wid}: расход запчасти")
            db.execute("UPDATE maintenance_work_parts SET operation_id=? WHERE id=?",(int(opid),int(p["id"])))
        plan=db.fetchone("SELECT * FROM maintenance_plans WHERE id=?",(int(work["plan_id"]),));interval=int(plan["interval_days"] or 0) if plan else 0;next_due=(datetime.now()+timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S") if interval else None
        with db.connect() as conn:
            cur=conn.execute("INSERT INTO maintenance_records(equipment_id,chat_id,actor_user_id,maintenance_type,status,next_due_at,note) VALUES(?,?,?,?,?,?,?)",(int(work["equipment_id"]),scope,int(actor_user_id),"planned","completed",next_due,str(result)[:1000]));rid=int(cur.lastrowid)
            conn.execute("UPDATE maintenance_work_orders SET status='completed',completed_at=CURRENT_TIMESTAMP,result=?,note=?,maintenance_record_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(result)[:1000],str(note)[:1000],rid,int(wid)))
            conn.execute("UPDATE maintenance_plans SET next_due_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(next_due,int(work["plan_id"])))
            conn.execute("UPDATE equipment SET last_service_at=CURRENT_TIMESTAMP,next_service_at=?,status='active',updated_at=CURRENT_TIMESTAMP WHERE id=?",(next_due,int(work["equipment_id"])));conn.commit()
    else:raise ValueError("Неизвестное действие.")
    return get_work_order(scope,wid,actor_user_id) or work

def snapshot(chat_id:int,user_id:int)->dict[str,Any]:
    plans=list_plans(chat_id,user_id); basic=list_work_orders(chat_id,user_id); work=[get_work_order(chat_id,int(x["id"]),user_id) or x for x in basic];now=datetime.now();return {"plans":plans,"work_orders":work,"counts":{"planned":sum(1 for x in work if x["status"]=="planned"),"in_progress":sum(1 for x in work if x["status"]=="in_progress"),"overdue":sum(1 for x in work if x["status"] in {"planned","in_progress"} and (_parse(x.get("due_at")) or datetime.max)<now)}}


def ensure_work_orders_for_all(now:datetime|None=None)->int:
    total=0
    for r in db.fetchall("SELECT DISTINCT chat_id FROM maintenance_plans WHERE is_enabled=1"):
        total+=ensure_work_orders(int(r["chat_id"]),now=now)
    return total
