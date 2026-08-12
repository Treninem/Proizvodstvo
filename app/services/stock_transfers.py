from __future__ import annotations
from typing import Any
from .. import db
from . import repository as repo, accounting

OPEN_STATUSES={"sent"}


def _scope(chat_id:int)->int:
    return repo.resolve_scope_chat_id(chat_id)


def _department_membership(scope:int,user_id:int,department_id:int|None,min_level:int=20)->bool:
    if department_id is None:return False
    return any(int(x.get('department_id') or 0)==int(department_id) and int(x.get('role_level') or 0)>=min_level for x in repo.user_department_memberships(scope,user_id))


def _can_send(scope:int,user_id:int,department_id:int|None)->bool:
    return repo.is_tenant_admin(scope,user_id) or _department_membership(scope,user_id,department_id,20)


def _can_receive(scope:int,user_id:int,department_id:int|None)->bool:
    return repo.is_tenant_admin(scope,user_id) or _department_membership(scope,user_id,department_id,20)


def _same_tenant(scope:int,table:str,obj_id:int|None)->bool:
    if obj_id is None:return True
    return bool(db.fetchone(f"SELECT id FROM {table} WHERE id=? AND chat_id=?",(int(obj_id),scope)))


def _validate_place(scope:int,area_id:int|None,department_id:int|None,location_id:int|None)->None:
    if area_id is not None and not _same_tenant(scope,'areas',area_id):raise ValueError('Выбран участок другой организации.')
    if department_id is not None and not _same_tenant(scope,'departments',department_id):raise ValueError('Выбран отдел другой организации.')
    if location_id is not None:
        row=db.fetchone('SELECT * FROM storage_locations WHERE id=? AND chat_id=? AND is_archived=0',(int(location_id),scope))
        if not row:raise ValueError('Место хранения не найдено.')
        if area_id is not None and row['area_id'] is not None and int(row['area_id'])!=int(area_id):raise ValueError('Место хранения относится к другому участку.')
        if department_id is not None and row['department_id'] is not None and int(row['department_id'])!=int(department_id):raise ValueError('Место хранения относится к другому отделу.')


def _entity(scope:int,entity_id:int):
    e=repo.get_entity(int(entity_id))
    if not e or int(e.chat_id)!=scope:raise ValueError('Позиция не найдена в этой организации.')
    return e


def _pending_reserved_conn(conn,scope:int,entity_type:str,entity_id:int,unit:str,area_id:int|None,department_id:int|None=None,location_id:int|None=None)->float:
    where=["t.chat_id=?","t.status='sent'","i.entity_type=?","i.entity_id=?","i.unit=?"]
    params:list[Any]=[scope,entity_type,int(entity_id),unit]
    if area_id is not None:where.append('t.from_area_id=?');params.append(int(area_id))
    if department_id is not None:where.append('t.from_department_id=?');params.append(int(department_id))
    if location_id is not None:where.append('t.from_location_id=?');params.append(int(location_id))
    r=conn.execute(f"SELECT COALESCE(SUM(i.sent_quantity),0) q FROM stock_transfer_items i JOIN stock_transfers t ON t.id=i.transfer_id WHERE {' AND '.join(where)}",tuple(params)).fetchone()
    return float(r['q'] if r else 0)


def reserved_outgoing(chat_id:int,entity_type:str,entity_id:int,unit:str,area_id:int|None,department_id:int|None=None,location_id:int|None=None)->float:
    scope=_scope(chat_id)
    with db.connect() as conn:return _pending_reserved_conn(conn,scope,entity_type,entity_id,unit,area_id,department_id,location_id)


def _physical_qty_conn(conn,scope:int,entity_type:str,entity_id:int,unit:str,area_id:int|None,department_id:int|None=None,location_id:int|None=None)->float:
    if department_id is not None or location_id is not None:
        where=['chat_id=?','entity_type=?','entity_id=?','unit=?'];params:list[Any]=[scope,entity_type,int(entity_id),unit]
        if area_id is not None:where.append('area_id=?');params.append(int(area_id))
        if department_id is not None:where.append('department_id=?');params.append(int(department_id))
        if location_id is not None:where.append('location_id=?');params.append(int(location_id))
        r=conn.execute(f"SELECT COALESCE(SUM(quantity),0) q FROM inventory_allocations WHERE {' AND '.join(where)}",tuple(params)).fetchone();return float(r['q'] if r else 0)
    r=conn.execute('SELECT quantity FROM inventory WHERE chat_id=? AND ((area_id IS NULL AND ? IS NULL) OR area_id=?) AND entity_type=? AND entity_id=? AND unit=?',(scope,area_id,area_id,entity_type,int(entity_id),unit)).fetchone();return float(r['quantity'] if r else 0)


def available_quantity(chat_id:int,entity_type:str,entity_id:int,unit:str,area_id:int|None,department_id:int|None=None,location_id:int|None=None)->float:
    scope=_scope(chat_id)
    with db.connect() as conn:
        return _physical_qty_conn(conn,scope,entity_type,entity_id,unit,area_id,department_id,location_id)-_pending_reserved_conn(conn,scope,entity_type,entity_id,unit,area_id,department_id,location_id)


def create_transfer(chat_id:int,user_id:int,*,from_area_id:int,to_area_id:int,items:list[dict],from_department_id:int|None=None,to_department_id:int|None=None,from_location_id:int|None=None,to_location_id:int|None=None,note:str='')->dict:
    scope=_scope(chat_id);account=repo.get_account_by_scope(scope)
    if not account or not repo.user_has_account_access(account.id,user_id):raise PermissionError('Нет доступа к этой организации.')
    _validate_place(scope,from_area_id,from_department_id,from_location_id);_validate_place(scope,to_area_id,to_department_id,to_location_id)
    if int(from_area_id)==int(to_area_id) and from_department_id==to_department_id and from_location_id==to_location_id:raise ValueError('Укажите другое место назначения.')
    if not _can_send(scope,user_id,from_department_id):raise PermissionError('Нет права передавать со стороны этого отдела.')
    combined:dict[tuple[int,str],tuple[Any,float,str]]={}
    for raw in items[:100]:
        eid=int(raw.get('entity_id') or 0);qty=float(raw.get('quantity') or 0)
        if eid<=0 or qty<=0:raise ValueError('Укажите позицию и количество больше нуля.')
        e=_entity(scope,eid);unit=str(raw.get('unit') or e.default_unit or 'шт')
        if repo.department_operation_allowed(scope,user_id,'movement','submit',e.entity_type,e.id) is False and not repo.is_tenant_admin(scope,user_id):raise PermissionError(f'Нет права передавать позицию «{e.name}».')
        key=(eid,unit);prev=combined.get(key);combined[key]=(e,(prev[1] if prev else 0)+qty,unit)
    if not combined:raise ValueError('Добавьте хотя бы одну позицию.')
    with db.connect() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            for e,qty,unit in combined.values():
                available=_physical_qty_conn(conn,scope,e.entity_type,e.id,unit,from_area_id,from_department_id,from_location_id)-_pending_reserved_conn(conn,scope,e.entity_type,e.id,unit,from_area_id,from_department_id,from_location_id)
                if qty>available+1e-9:raise ValueError(f'Недостаточно «{e.name}»: доступно {available:g} {unit}, запрошено {qty:g}.')
            cur=conn.execute("""INSERT INTO stock_transfers(chat_id,from_area_id,to_area_id,from_department_id,to_department_id,from_location_id,to_location_id,status,sent_by,note)
                VALUES(?,?,?,?,?,?,?,'sent',?,?)""",(scope,from_area_id,to_area_id,from_department_id,to_department_id,from_location_id,to_location_id,int(user_id),str(note or '')[:1000]))
            tid=int(cur.lastrowid)
            for e,qty,unit in combined.values():conn.execute('INSERT INTO stock_transfer_items(transfer_id,entity_type,entity_id,unit,sent_quantity) VALUES(?,?,?,?,?)',(tid,e.entity_type,e.id,unit,qty))
            conn.commit()
        except Exception:
            conn.rollback();raise
    repo.tenant_audit(scope,user_id,'transfer_sent','stock_transfer',str(tid),f'{from_area_id}->{to_area_id}; items={len(combined)}')
    return get_transfer(scope,tid) or {'id':tid}


def get_transfer(chat_id:int,transfer_id:int)->dict|None:
    scope=_scope(chat_id)
    row=db.fetchone("""SELECT t.*,fa.name from_area_name,ta.name to_area_name,fd.name from_department_name,td.name to_department_name,
                              fl.name from_location_name,tl.name to_location_name
                       FROM stock_transfers t LEFT JOIN areas fa ON fa.id=t.from_area_id AND fa.chat_id=t.chat_id LEFT JOIN areas ta ON ta.id=t.to_area_id AND ta.chat_id=t.chat_id
                       LEFT JOIN departments fd ON fd.id=t.from_department_id AND fd.chat_id=t.chat_id LEFT JOIN departments td ON td.id=t.to_department_id AND td.chat_id=t.chat_id
                       LEFT JOIN storage_locations fl ON fl.id=t.from_location_id AND fl.chat_id=t.chat_id LEFT JOIN storage_locations tl ON tl.id=t.to_location_id AND tl.chat_id=t.chat_id
                       WHERE t.id=? AND t.chat_id=?""",(int(transfer_id),scope))
    if not row:return None
    d=dict(row);d['items']=[dict(x) for x in db.fetchall('SELECT i.*,e.name entity_name FROM stock_transfer_items i JOIN stock_transfers t ON t.id=i.transfer_id JOIN entities e ON e.id=i.entity_id AND e.chat_id=t.chat_id WHERE i.transfer_id=? AND t.chat_id=? ORDER BY e.name',(int(transfer_id),scope))];return d


def list_transfers(chat_id:int,user_id:int,status:str|None=None,limit:int=100)->list[dict]:
    scope=_scope(chat_id);account=repo.get_account_by_scope(scope)
    if not account or not repo.user_has_account_access(account.id,user_id):raise PermissionError('Нет доступа.')
    memberships={int(x['department_id']) for x in repo.user_department_memberships(scope,user_id)};where=['chat_id=?'];params:list[Any]=[scope]
    if status:where.append('status=?');params.append(status)
    if not repo.is_tenant_admin(scope,user_id):
        if memberships:
            marks=','.join('?' for _ in memberships);where.append(f'(from_department_id IN ({marks}) OR to_department_id IN ({marks}) OR sent_by=?)');params.extend(sorted(memberships));params.extend(sorted(memberships));params.append(int(user_id))
        else:where.append('sent_by=?');params.append(int(user_id))
    params.append(max(1,min(int(limit),300)));ids=db.fetchall(f"SELECT id FROM stock_transfers WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",tuple(params));return [x for r in ids if (x:=get_transfer(scope,int(r['id']))) is not None]


def accept_transfer(chat_id:int,user_id:int,transfer_id:int,actual_items:list[dict]|None=None,note:str='')->dict:
    """Atomic receiving: transfer status and all inventory changes commit together."""
    from . import reliability
    scope=_scope(chat_id);t=get_transfer(scope,transfer_id)
    if not t:raise ValueError('Передача не найдена.')
    if t['status'] not in OPEN_STATUSES:raise ValueError('Передача уже обработана.')
    if not _can_receive(scope,user_id,t.get('to_department_id')):raise PermissionError('Нет права принять передачу для этого отдела.')
    supplied={int(x.get('item_id') or x.get('id') or 0):float(x.get('quantity') or 0) for x in (actual_items or [])};decisions=[];has_diff=False
    for item in t['items']:
        actual=supplied.get(int(item['id']),float(item['sent_quantity']))
        if actual<0 or actual>float(item['sent_quantity'])+1e-9:raise ValueError('Фактическое количество должно быть от 0 до переданного количества.')
        if abs(actual-float(item['sent_quantity']))>1e-9:has_diff=True
        decisions.append((item,actual))
    if has_diff and not str(note or '').strip():raise ValueError('При расхождении обязательно укажите причину.')
    op_ids=[];status='accepted_discrepancy' if has_diff else 'accepted'
    with db.connect() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE');current=conn.execute('SELECT status FROM stock_transfers WHERE id=? AND chat_id=?',(int(transfer_id),scope)).fetchone()
            if not current or current['status']!='sent':raise ValueError('Передача уже обработана другим пользователем.')
            for item,actual in decisions:
                if actual>0:
                    available=_physical_qty_conn(conn,scope,item['entity_type'],int(item['entity_id']),item['unit'],t['from_area_id'],t.get('from_department_id'),t.get('from_location_id'))
                    if actual>available+1e-9:raise ValueError(f"Недостаточно «{item['entity_name']}» в месте выдачи: {available:g} {item['unit']}.")
                conn.execute('UPDATE stock_transfer_items SET received_quantity=?,difference_quantity=?,discrepancy_reason=? WHERE id=?',(actual,actual-float(item['sent_quantity']),str(note or '')[:1000] if abs(actual-float(item['sent_quantity']))>1e-9 else '',int(item['id'])))
                if actual<=0:continue
                op={'operation_type':'movement','entity_type':item['entity_type'],'entity_id':int(item['entity_id']),'quantity':actual,'unit':item['unit'],'from_area_id':int(t['from_area_id']),'to_area_id':int(t['to_area_id']),'area_id':int(t['to_area_id']),'from_department_id':t.get('from_department_id'),'to_department_id':t.get('to_department_id'),'from_location_id':t.get('from_location_id'),'to_location_id':t.get('to_location_id'),'client_request_id':f'transfer:{transfer_id}:item:{item["id"]}','source_channel':'transfer'}
                cur=conn.execute("""INSERT INTO operations(chat_id,group_chat_id,area_id,user_id,operation_type,entity_type,entity_id,quantity,unit,raw_text,from_area_id,to_area_id,destination_type,storage_place,client_request_id,source_channel,task_id,lot_id,department_id,from_department_id,to_department_id,storage_location_id,from_location_id,to_location_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(scope,scope,int(t['to_area_id']),int(user_id),'movement',item['entity_type'],int(item['entity_id']),actual,item['unit'],f'Принятие передачи №{transfer_id}',int(t['from_area_id']),int(t['to_area_id']),'','',op['client_request_id'],'transfer',None,None,None,t.get('from_department_id'),t.get('to_department_id'),None,t.get('from_location_id'),t.get('to_location_id')))
                oid=int(cur.lastrowid);op_ids.append(oid);accounting._apply_inventory_effect(scope,op,conn=conn);reliability.queue_operation_steps(conn,scope,oid,user_id,op,f'Принятие передачи №{transfer_id}')
            conn.execute('UPDATE stock_transfers SET status=?,accepted_by=?,receiver_note=?,accepted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND chat_id=?',(status,int(user_id),str(note or '')[:1000],int(transfer_id),scope));conn.commit()
        except Exception:
            conn.rollback();raise
    for oid in op_ids:
        try:reliability.process_for_operation(oid)
        except Exception:pass
    repo.tenant_audit(scope,user_id,'transfer_accepted','stock_transfer',str(transfer_id),f'status={status}; operations={op_ids}',severity='warning' if has_diff else 'info')
    return get_transfer(scope,transfer_id) or {}
