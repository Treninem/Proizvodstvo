const params = new URLSearchParams(location.search);
const tg = window.Telegram?.WebApp || null;
try { tg?.ready?.(); tg?.expand?.(); } catch(e) {}
let chatId = params.get('chat_id') || localStorage.getItem('prodMiniChatId') || '';
let userId = params.get('user_id') || params.get('uid') || localStorage.getItem('prodMiniUserId') || String(tg?.initDataUnsafe?.user?.id || '');
const token = params.get('token') || '';
const initData = tg?.initData || '';
const headers = {'Content-Type':'application/json'};
if (token) headers['X-Access-Token'] = token;
if (initData) headers['X-Telegram-Init-Data'] = initData;
const byId = (id) => document.getElementById(id);
const fmt = (n) => (Number(n || 0)).toLocaleString('ru-RU');
let state = {
  accounts:[], entities:{}, areas:[], destinations:[], permissions:{}, area_access:{},
  job_titles:[], workers:[], area_access_rules:[], inventory_positions:[], inventory_history:[],
  inventory_sessions:[], active_inventory_session:null, report_presets:[], report_schedules:[], report_delivery_history:[],
  inbox_items:[], worker_activity:[], worker_shifts:[], shift_plans:[], shift_templates:[], shift_calendar:[], attendance_deviations:[], attendance_summary:[], attendance_details:[], notification_preferences:{}, can_manage:false, plan_targets:[], dashboard:null,
  departments:[], department_memberships:[], work_access:[], can_manage_departments:false, is_system_admin:false, stock_risk:{rules:[],incidents:[],events:[],observations:[],event_catalog:[],summary:{}},
  audit:{site_actions:[],sync_events:[]}, security:null
};
const sectionLabels={overview:'Обзор',production:'Выпуск',material:'Сырьё',assembly:'Сборка',movement:'Перемещение',shipment:'Отгрузка',returns:'Возврат',reports:'Отчёты',inventory:'Инвентаризация'};
const destinationLabels={storage:'Место хранения',client:'Клиент',fulfillment:'Фулфилмент',other:'Другое'};
const operationLabels={production:'Изготовление',material_in:'Приход материалов',material_out:'Расход материалов',energy:'Показания счётчика',assembly:'Сборка изделия',movement:'Перемещение',transfer_to_assembly:'Передача на следующий этап',shipment:'Отправка',shipment_client:'Передача заказчику',shipment_fulfillment:'Передача на внешний склад',return:'Возврат',stock_in:'Приход позиции',stock_out:'Расход складской позиции',write_off:'Списание',inventory_adjust:'Инвентаризация',shifts:'Рабочие смены'};
const permissionLabels={production:'выпуск',material:'сырьё',assembly:'сборка',movement:'перемещение',shipment:'отгрузка',fulfillment:'фулфилмент',returns:'возвраты',stock:'остатки',reports:'отчёты',export:'экспорт',edit:'исправления',workers:'сотрудники',permissions:'доступы',setup:'настройки',energy:'энергия'};

const departmentOperationOptions=['production','material_in','material_out','energy','assembly','movement','transfer_to_assembly','shipment','shipment_client','shipment_fulfillment','return','stock_in','stock_out','write_off','inventory_adjust','shifts'];
const departmentRoleLabels={10:'Только просмотр',20:'Ввод данных',30:'Ввод и исправление',50:'Руководитель'};
function can(section){ if(!section) return true; const p=state.permissions||{}; return !!(p[section] || p.setup || p.permissions || p.grant); }
function typeSum(items, names){return (items || []).filter(x => names.includes(x.type)).reduce((s,x)=>s+Number(x.qty||0),0);}
function escapeHtml(v){return String(v ?? '').replace(/[&<>"]/g, s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[s]));}
function renderRows(el, rows, empty='Нет данных'){
  if(!el) return; el.innerHTML = '';
  if(!rows || !rows.length){ el.textContent = empty; el.classList.add('empty'); return; }
  el.classList.remove('empty');
  rows.forEach(r=>{const node=document.createElement('div'); node.className='row '+(r.flag||''); node.innerHTML=`<b>${escapeHtml(r.name)}</b><span>${escapeHtml(r.value)}</span>`; el.appendChild(node);});
}
function showNotice(text, bad=false){const n=byId('notice'); if(!n)return; n.textContent=text; n.className='notice'+(bad?' bad':''); setTimeout(()=>n.classList.add('hidden'), 4200);}
function showTab(tab){
  const target = byId(`page-${tab}`); if(target?.classList.contains('hidden-by-access')){showNotice('Раздел недоступен.', true); return;}
  document.querySelectorAll('.tab,.card[data-tab]').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
  document.querySelectorAll('.tab-page').forEach(x=>x.classList.toggle('active',x.id===`page-${tab}`));
  localStorage.setItem('prodMiniTab', tab);
}
function optionList(list, empty='Не выбрано'){return `<option value="">${escapeHtml(empty)}</option>`+(list||[]).map(x=>`<option value="${x.id}">${escapeHtml(x.name)}</option>`).join('');}
function fillSelect(id, list, empty){const el=byId(id); if(!el)return; const previous=el.value; el.innerHTML=optionList(list, empty); if([...el.options].some(x=>x.value===previous))el.value=previous;}
function entity(type){return state.entities?.[type] || [];}
function entityTypeById(id){ if(entity('component').some(x=>String(x.id)===String(id))) return 'component'; if(entity('stock_item').some(x=>String(x.id)===String(id))) return 'stock_item'; return 'component';}
function areaChoices(section, action='view'){
  const access=state.area_access?.[section]||{};
  if(!access.restricted) return state.areas||[];
  const allowed=new Set((access[action]||[]).map(String));
  return (state.areas||[]).filter(x=>allowed.has(String(x.id)));
}
function workAccessItem(){return (state.work_access||[]).find(x=>x.operation_key===val('workOperation'))||null;}
function renderWorkEntry(){
  const access=state.work_access||[];
  const tab=document.querySelector('[data-work-entry]');
  tab?.classList.toggle('hidden',!access.length);
  fillSelect('workOperation',access.map(x=>({id:x.operation_key,name:operationLabels[x.operation_key]||x.operation_key})),'Действие');
  if(access.length && !val('workOperation')) byId('workOperation').value=access[0].operation_key;
  updateWorkEntry();
}
function updateWorkEntry(){
  const item=workAccessItem();
  fillSelect('workEntity',(item?.entities||[]).map(x=>({id:x.id,name:x.name})),'Позиция');
  const movement=['movement','transfer_to_assembly'].includes(item?.operation_key||'');
  byId('workSingleArea')?.classList.toggle('hidden',movement);
  byId('workMoveAreas')?.classList.toggle('hidden',!movement);
}
async function saveWorkEntry(){
  const item=workAccessItem(), entityId=val('workEntity'), amount=qty('workQuantity');
  const selected=(item?.entities||[]).find(x=>String(x.id)===String(entityId));
  if(!item||!selected||!amount||amount<=0){showNotice('Выберите действие, позицию и количество.',true);return;}
  const movement=['movement','transfer_to_assembly'].includes(item.operation_key);
  const body={chat_id:Number(chatId),user_id:Number(userId),operation_type:item.operation_key,entity_type:selected.type,entity_id:Number(selected.id),quantity:amount,unit:selected.unit||'шт',note:val('workNote')};
  if(movement){body.from_area_id=val('workFromArea')?Number(val('workFromArea')):null;body.to_area_id=val('workToArea')?Number(val('workToArea')):null;if(!body.from_area_id||!body.to_area_id){showNotice('Выберите площадки отправления и получения.',true);return;}}
  else body.area_id=val('workArea')?Number(val('workArea')):null;
  const res=await fetch('/api/operations',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));
  if(!res.ok){showNotice(data.detail==='department access denied'?'Действие или позиция не разрешены.':(data.detail||'Запись не сохранена.'),true);return;}
  byId('workQuantity').value='';byId('workNote').value='';showNotice('Сохранено.');await load();
}
function updateMoveEntities(){const t=byId('moveType')?.value||'component'; fillSelect('moveEntity', entity(t), 'Позиция');}
function destinationsByType(types){const allowed=new Set(types); return (state.destinations||[]).filter(x=>allowed.has(x.destination_type));}
function destinationName(id){return (state.destinations||[]).find(x=>String(x.id)===String(id))?.name||'';}
function updateShipDestinations(){
  const kind=val('shipKind');
  const type=kind==='shipment_fulfillment'?'fulfillment':'client';
  fillSelect('shipDestination', destinationsByType([type]), type==='client'?'Клиент не выбран':'Фулфилмент не выбран');
}
function applyAccess(){
  document.querySelectorAll('[data-section]').forEach(el=>{ el.classList.toggle('hidden-by-access', !can(el.dataset.section)); });
  const active = document.querySelector('.tab-page.active');
  if(active?.classList.contains('hidden-by-access')) showTab((state.work_access||[]).length?'work':'overview');
  if((state.work_access||[]).length && localStorage.getItem('prodMiniTab')==='overview') showTab('work');
  const allowed = Object.keys(state.permissions||{}).filter(k=>state.permissions[k]);
  const restricted = Object.values(state.area_access||{}).some(x=>x?.restricted);
  byId('rolePill').textContent = restricted ? 'Доступ по площадкам' : (allowed.length ? 'Доступ настроен' : 'Ограниченный доступ');

  document.querySelectorAll('[data-admin-only]').forEach(el=>el.classList.toggle('hidden',!state.is_system_admin));
  document.querySelectorAll('[data-department-manage]').forEach(el=>el.classList.toggle('hidden',!state.can_manage_departments));
  const departmentMode=!state.is_system_admin && (state.department_memberships||[]).length>0;
  document.querySelector('[data-tab="overview"]')?.classList.toggle('hidden',departmentMode);
  document.querySelector('.summary-grid')?.classList.toggle('hidden',departmentMode);
  if(departmentMode){
    document.querySelectorAll('.tab[data-section]').forEach(el=>{
      const keep=(el.dataset.tab==='inventory' && can('stock')) || el.dataset.tab==='risks';
      el.classList.toggle('hidden-by-access',!keep);
    });
    document.querySelectorAll('.card[data-section]').forEach(el=>el.classList.add('hidden-by-access'));
  }
  applyRiskEventAccess();
}
function fillForms(){
  fillSelect('workArea', state.areas||[], 'Без площадки');
  fillSelect('workFromArea', state.areas||[], 'Откуда');
  fillSelect('workToArea', state.areas||[], 'Куда');
  fillSelect('productionArea', areaChoices('production','submit'), 'Без площадки');
  fillSelect('materialArea', areaChoices('material','submit'), 'Без площадки');
  fillSelect('assemblyArea', areaChoices('assembly','submit'), 'Без площадки');
  fillSelect('moveFrom', areaChoices('movement','submit'), 'Откуда');
  fillSelect('moveTo', areaChoices('movement','submit'), 'Куда');
  fillSelect('shipArea', areaChoices('shipment','submit'), 'Без площадки');
  fillSelect('returnArea', areaChoices('returns','submit'), 'Без площадки');
  fillSelect('reportArea', areaChoices('reports','view'), 'Все доступные площадки');
  fillSelect('inventoryArea', areaChoices('inventory','view'), 'Площадка');
  fillSelect('productionEntity', [...entity('component'), ...entity('stock_item')], 'Позиция');
  fillSelect('materialEntity', entity('material'), 'Сырьё');
  fillSelect('assemblyEntity', entity('product'), 'Изделие');
  fillSelect('shipEntity', entity('product'), 'Изделие');
  fillSelect('returnEntity', entity('product'), 'Изделие');
  fillSelect('planProduct', entity('product'), 'Изделие');
  fillSelect('assemblyStorage', destinationsByType(['storage']), 'Место не выбрано');
  fillSelect('returnStorage', destinationsByType(['storage']), 'Место не выбрано');
  fillSelect('accessJob', (state.job_titles||[]).map(x=>({id:x.id,name:x.name})), 'Должность');
  fillSelect('accessArea', state.areas||[], 'Площадка');
  fillSelect('workerJob', (state.job_titles||[]).map(x=>({id:x.id,name:x.name})), 'Должность');
  renderDepartmentSelects();
  renderWorkEntry();
  fillSelect('inventorySessionArea', areaChoices('inventory','submit'), 'Площадка');
  fillSelect('schedulePreset', (state.report_presets||[]).map(x=>({id:x.id,name:x.name})), 'Шаблон');
  const shiftWorkers=(state.workers||[]).map(x=>({id:x.user_id,name:x.display_name||String(x.user_id)}));
  if(userId && !shiftWorkers.some(x=>String(x.id)===String(userId))) shiftWorkers.unshift({id:userId,name:'Моя смена'});
  fillSelect('shiftWorker', shiftWorkers, 'Моя смена');
  fillSelect('shiftArea', state.areas||[], 'Без площадки');
  fillSelect('shiftPlanWorker', shiftWorkers, 'Сотрудник');
  fillSelect('shiftPlanArea', state.areas||[], 'Без площадки');
  fillSelect('shiftTemplateWorker', shiftWorkers, 'Сотрудник');
  fillSelect('shiftTemplateArea', state.areas||[], 'Без площадки');
  fillSelect('shiftCalendarWorker', shiftWorkers, 'Все сотрудники');
  fillSelect('shiftCalendarArea', state.areas||[], 'Все площадки');
  fillSelect('attendanceWorker', shiftWorkers, 'Все сотрудники');
  fillSelect('attendanceArea', areaChoices('reports','view'), 'Все площадки');
  if(byId('shiftWorker') && !byId('shiftWorker').value && userId) byId('shiftWorker').value=String(userId);
  if(byId('scheduleChatId') && !byId('scheduleChatId').value && userId) byId('scheduleChatId').value=String(userId);
  fillSelect('riskObservationArea',state.areas||[],'Без площадки');
  fillSelect('riskEventArea',state.areas||[],'Все площадки');
  fillSelect('riskEventDepartment',(state.departments||[]).map(x=>({id:x.id,name:x.name})),'Без отдела');
  fillSelect('stockRuleArea',state.areas||[],'Все площадки');
  fillSelect('stockRuleYieldEntity',riskAllEntities(),'Не рассчитывать выход');fillSelect('stockRulePlannedEntity',entity('product'),'Без плана выпуска');
  fillSelect('riskEventType',(state.stock_risk?.event_catalog||[]).map(x=>({id:x.key,name:x.label})),'Тип события');
  updateRiskObservationEntities(); updateRiskRuleEntities(); updateRiskEventEntities();
  if(byId('riskEventStart')&&!byId('riskEventStart').value){const d=new Date();byId('riskEventStart').value=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
  updateMoveEntities(); updateShipDestinations(); updateInventoryEntities(); updateInventorySessionEntities(); updateScheduleFields();
  setStep68DateDefaults(); renderPlan(); renderDestinations(); renderAreaAccess(); renderTeam(); renderInventoryPositions(); renderReportPresets(); renderInventorySessions(); renderReportSchedules(); renderReportDeliveryHistory(); renderInbox(); renderNotificationPreferences(); renderWorkerActivity(); renderShiftPlans(); renderShiftTemplates(); renderShiftCalendar(); renderAttendanceSummary(); renderStockRisk();
}
function renderDashboard(data){
  const totals = data.month_totals || [];
  byId('productionCount').textContent = fmt(typeSum(totals, ['production']));
  byId('assemblyCount').textContent = fmt(typeSum(totals, ['assembly']));
  byId('shipmentCount').textContent = fmt(typeSum(totals, ['shipment','shipment_client','shipment_fulfillment']));
  byId('alertCount').textContent = (data.alerts || []).length;
  byId('syncPill').textContent = data.updated_at ? `Обновлено ${String(data.updated_at).slice(11,16)}` : 'Обновление —';
  renderRows(byId('alerts'), (data.alerts||[]).slice(0,8).map(x=>({name:x.area_name?`${x.area_name} · ${x.name}`:x.name, value:`${x.stock_text} ${x.unit} · ${x.days_left_text} дн.`, flag:x.flag?'flag-red':''})), 'Сырьё не настроено');
  const stock=[]; ['component','material','product','stock_item'].forEach(k=> (data.inventory?.[k]||[]).slice(0,6).forEach(x=>stock.push({name:x.name, value:`${x.qty_text} ${x.unit}`})));
  renderRows(byId('stock'), stock.slice(0,14), 'Остатков пока нет');
  renderRows(byId('areaSummary'), (data.area_summary||[]).map(x=>({name:x.area_name, value:`компл. ${x.components_text} · сырьё ${x.materials_text} · готово ${x.products_text}`})), 'Площадки пока пустые');
  renderRows(byId('materialAreaDays'), (data.material_days_by_area||[]).slice(0,14).map(x=>({name:`${x.area_name} · ${x.name}`, value:`${x.stock_text} ${x.unit} · ${x.days_left_text} дн.`, flag:x.flag?'flag-red':''})), 'Сырьё по площадкам не настроено');
  renderRows(byId('recent'), (data.recent||[]).map(x=>({name:x.area?`${x.area} · ${x.name || x.type}`:(x.name || x.type), value:`${x.qty_text} ${x.unit}${x.storage_place?` · ${x.storage_place}`:''}`})), 'Записей пока нет');
  renderRows(byId('materialDays'), (data.material_days_by_area||data.material_days||[]).map(x=>({name:x.area_name?`${x.area_name} · ${x.name}`:x.name, value:`${x.stock_text} ${x.unit} · ${x.days_left_text} дн.`, flag:x.flag?'flag-red':''})), 'Сырьё не настроено');
}
function renderPlan(){ renderRows(byId('planList'), (state.plan_targets||[]).map(x=>({name:x.product_name, value:fmt(x.target_qty)})), 'План не выбран'); }
function renderAudit(){
  renderRows(byId('syncList'), (state.audit?.sync_events||[]).map(x=>({name:x.source, value:`${x.status} · ${String(x.created_at||'').slice(0,16)}`})), 'Синхронизации пока нет');
  renderRows(byId('miniAppLog'), (state.audit?.site_actions||[]).map(x=>({name:x.action, value:String(x.created_at||'').slice(0,16)})), 'Действий пока нет');
}
function renderSecurity(){
  const s=state.security||{};
  renderRows(byId('securityStatus'), [
    {name:'Вход', value:s.protected_access?'защищён':'локальный режим'},
    {name:'Копии', value:s.encrypted_backups?'шифруются':'обычные'},
    {name:'Учёты', value:s.account_separated?'разделены':'—'},
    {name:'Резервный вход', value:s.site_password_access?'готов':'не задан'},
    {name:'ID учёта для .env', value:String(state.scope_chat_id||'—')}
  ], 'Нет данных');
}
function renderDestinations(){
  const box=byId('destinationList'); if(!box)return; box.innerHTML='';
  if(!(state.destinations||[]).length){box.textContent='Места ещё не созданы';box.classList.add('empty');return;}
  box.classList.remove('empty');
  (state.destinations||[]).forEach(item=>{const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.name)}</b><span>${escapeHtml(destinationLabels[item.destination_type]||item.destination_type)}</span></div><div class="mini-actions"><button data-edit-destination="${item.id}">Изменить</button><button class="danger" data-delete-destination="${item.id}">Удалить</button></div>`;box.appendChild(row);});
}
function renderAreaAccess(){
  const box=byId('areaAccessList'); if(!box)return; box.innerHTML='';
  const rules=state.area_access_rules||[];
  if(!rules.length){box.textContent='Правила ещё не заданы';box.classList.add('empty');return;}
  box.classList.remove('empty');
  rules.forEach(item=>{const flags=[item.can_view?'просмотр':'',item.can_submit?'добавление':'',item.can_edit?'редактирование':''].filter(Boolean).join(', ')||'нет действий';const key=`${item.job_title_id}:${item.area_id}:${item.section_key}`;const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.job_title_name)} · ${escapeHtml(item.area_name)}</b><span>${escapeHtml(sectionLabels[item.section_key]||item.section_key)} · ${escapeHtml(flags)}</span></div><div class="mini-actions"><button data-edit-area-rule="${escapeHtml(key)}">Изменить</button><button class="danger" data-delete-area-rule="${escapeHtml(key)}">Удалить</button></div>`;box.appendChild(row);});
}
function renderTeam(){
  const jobs=byId('jobTitleList'); if(jobs){jobs.innerHTML=''; const list=state.job_titles||[]; if(!list.length){jobs.textContent='Должности ещё не созданы';jobs.classList.add('empty');}else{jobs.classList.remove('empty');list.forEach(item=>{const perms=Object.entries(item.permissions||{}).filter(([,v])=>v).map(([k])=>permissionLabels[k]||k).join(', ')||'без прав';const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.name)}</b><span>${escapeHtml(perms)}</span></div><div class="mini-actions"><button data-edit-job-title="${item.id}">Изменить</button><button class="danger" data-delete-job-title="${item.id}">Удалить</button></div>`;jobs.appendChild(row);});}}
  const workers=byId('workerList'); if(workers){workers.innerHTML=''; const list=state.workers||[]; if(!list.length){workers.textContent='Сотрудники ещё не добавлены';workers.classList.add('empty');}else{workers.classList.remove('empty');list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.display_name||item.user_id)}</b><span>ID ${escapeHtml(item.user_id)} · ${escapeHtml(item.job_name||'без должности')}</span></div><div class="mini-actions"><button data-edit-worker="${item.user_id}">Изменить</button><button class="danger" data-delete-worker="${item.user_id}">Отключить</button></div>`;workers.appendChild(row);});}}
}
function clearJobTitle(){byId('jobTitleId').value='';byId('jobTitleName').value='';document.querySelectorAll('[data-job-permission]').forEach(x=>x.checked=false);}
function clearWorker(){byId('workerUserId').value='';byId('workerName').value='';byId('workerJob').value='';byId('workerUserId').readOnly=false;}
function currentJobPermissions(){const result={};document.querySelectorAll('[data-job-permission]').forEach(x=>{result[x.dataset.jobPermission]=!!x.checked;});return result;}
function updateInventoryEntities(){const type=val('inventoryType')||'component';fillSelect('inventoryEntity',entity(type),'Позиция');updateInventoryCurrent();}
function selectedInventoryPosition(){return (state.inventory_positions||[]).find(x=>String(x.area_id)===String(val('inventoryArea'))&&String(x.entity_type)===String(val('inventoryType'))&&String(x.entity_id)===String(val('inventoryEntity')));}
function updateInventoryCurrent(){const row=selectedInventoryPosition();if(byId('inventoryCurrent'))byId('inventoryCurrent').value=row?`${fmt(row.quantity)} ${row.unit}`:'0';}
function renderInventoryPositions(){
  const box=byId('inventoryPositions'); if(!box)return; box.innerHTML=''; const rows=state.inventory_positions||[];
  if(!rows.length){box.textContent='Остатков пока нет';box.classList.add('empty');return;} box.classList.remove('empty');
  rows.forEach(item=>{const key=`${item.area_id||''}:${item.entity_type}:${item.entity_id}`;const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.area_name||'Общий склад')} · ${escapeHtml(item.entity_name)}</b><span>${escapeHtml(fmt(item.quantity))} ${escapeHtml(item.unit)}</span></div><div class="mini-actions"><button data-select-inventory="${escapeHtml(key)}">Выбрать</button></div>`;box.appendChild(row);});
}
function renderInventoryHistory(){
  const box=byId('inventoryHistory'); if(!box)return; box.innerHTML=''; const rows=state.inventory_history||[];
  if(!rows.length){box.textContent='Записей для выбранной позиции нет';box.classList.add('empty');return;} box.classList.remove('empty');
  rows.forEach(item=>{const area=item.area_name||(item.from_area_name&&item.to_area_name?`${item.from_area_name} → ${item.to_area_name}`:'без площадки');const note=item.raw_text?` · ${item.raw_text}`:'';const corrected=item.is_corrected?' · исправлена':'';const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>№${item.id} · ${escapeHtml(operationLabels[item.operation_type]||item.operation_type)}</b><span>${escapeHtml(item.entity_name||'позиция')} · ${escapeHtml(fmt(item.quantity))} ${escapeHtml(item.unit)} · ${escapeHtml(area)}${escapeHtml(corrected)}${escapeHtml(note)}</span></div><span>${escapeHtml(String(item.created_at||'').slice(0,16))}</span>`;box.appendChild(row);});
}
function renderReportPresets(){
  const box=byId('reportPresetList'); if(!box)return; box.innerHTML=''; const list=state.report_presets||[];
  if(!list.length){box.textContent='Шаблонов пока нет';box.classList.add('empty');return;} box.classList.remove('empty');
  list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.name)}</b><span>${escapeHtml(item.request_text)} · ${item.report_format==='pdf'?'PDF':'Excel'}${item.area_name?` · ${escapeHtml(item.area_name)}`:''}</span></div><div class="mini-actions"><button data-use-report-preset="${item.id}">Применить</button><button data-edit-report-preset="${item.id}">Изменить</button><button class="danger" data-delete-report-preset="${item.id}">Удалить</button></div>`;box.appendChild(row);});
}
function clearReportPreset(){byId('reportPresetId').value='';byId('reportPresetName').value='';byId('reportPresetFormat').value='xlsx';}
function applyReportPreset(item, edit=false){if(!item)return;byId('reportText').value=item.request_text||'отчёт за месяц';byId('reportArea').value=item.area_id?String(item.area_id):'';byId('reportPresetFormat').value=item.report_format||'xlsx';if(edit){byId('reportPresetId').value=item.id;byId('reportPresetName').value=item.name||'';}showTab('reports');}

const inventorySessionStatusLabels={draft:'Черновик',submitted:'Ожидает подтверждения',approved:'Подтверждена',rejected:'Отклонена',cancelled:'Отменена'};
const scheduleFrequencyLabels={daily:'Ежедневно',weekly:'Еженедельно',monthly:'Ежемесячно'};
function updateInventorySessionEntities(){const type=val('inventorySessionType')||'component';fillSelect('inventorySessionEntity',entity(type),'Позиция');}
function renderInventorySessions(){
  const listBox=byId('inventorySessionList');if(listBox){listBox.innerHTML='';const list=state.inventory_sessions||[];if(!list.length){listBox.textContent='Сессий пока нет';listBox.classList.add('empty');}else{listBox.classList.remove('empty');list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';const difference=Number(item.counted_difference||0);row.innerHTML=`<div><b>№${item.id} · ${escapeHtml(item.area_name||'Площадка')}</b><span>${escapeHtml(inventorySessionStatusLabels[item.status]||item.status)} · позиций ${escapeHtml(item.item_count||0)}${difference?` · расхождение ${escapeHtml(fmt(difference))}`:''} · ${escapeHtml(item.creator_name||item.created_by)}</span></div><div class="mini-actions"><button data-open-inventory-session="${item.id}">Открыть</button></div>`;listBox.appendChild(row);});}}
  const box=byId('inventorySessionItems');if(!box)return;box.innerHTML='';const session=state.active_inventory_session;
  if(!session){box.textContent='Пересчёт не выбран';box.classList.add('empty');return;}box.classList.remove('empty');
  const head=document.createElement('div');head.className='manager-row';head.innerHTML=`<div><b>№${session.id} · ${escapeHtml(session.area_name)}</b><span>${escapeHtml(inventorySessionStatusLabels[session.status]||session.status)}${session.note?` · ${escapeHtml(session.note)}`:''}</span></div>`;box.appendChild(head);
  (session.items||[]).forEach(item=>{const delta=Number(item.actual_quantity||0)-Number(item.system_quantity||0);const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.entity_name)}</b><span>в базе ${escapeHtml(fmt(item.system_quantity))} · фактически ${escapeHtml(fmt(item.actual_quantity))} ${escapeHtml(item.unit)} · разница ${escapeHtml(fmt(delta))}</span></div>${session.status==='draft'?`<div class="mini-actions"><button class="danger" data-delete-inventory-session-item="${item.id}">Убрать</button></div>`:''}`;box.appendChild(row);});
  if(!(session.items||[]).length){const empty=document.createElement('div');empty.className='row';empty.textContent='Позиции ещё не добавлены';box.appendChild(empty);}
}
function clearInventorySession(){state.active_inventory_session=null;if(byId('inventorySessionId'))byId('inventorySessionId').value='';if(byId('inventorySessionNote'))byId('inventorySessionNote').value='';renderInventorySessions();}
async function openInventorySession(id){const res=await fetch(`/api/inventory-sessions?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(id)}`,{headers});if(!res.ok){showNotice('Инвентаризация недоступна.',true);return;}const data=await res.json();state.active_inventory_session=data.session||null;if(state.active_inventory_session){byId('inventorySessionId').value=state.active_inventory_session.id;byId('inventorySessionArea').value=String(state.active_inventory_session.area_id);byId('inventorySessionNote').value=state.active_inventory_session.note||'';}renderInventorySessions();showTab('inventory');}
function renderReportSchedules(){const box=byId('reportScheduleList');if(!box)return;box.innerHTML='';const list=state.report_schedules||[];if(!list.length){box.textContent='Расписаний пока нет';box.classList.add('empty');return;}box.classList.remove('empty');list.forEach(item=>{const status=item.last_status==='sent'?'отправлен':item.last_status==='error'?'ошибка':item.last_status==='running'?'выполняется':'ещё не отправлялся';const zone=item.timezone_name==='server'?'время сервера':(item.timezone_name||'время сервера');const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.preset_name)}</b><span>${escapeHtml(scheduleFrequencyLabels[item.frequency]||item.frequency)} · ${String(item.hour).padStart(2,'0')}:${String(item.minute).padStart(2,'0')} · ${escapeHtml(zone)} · следующая ${escapeHtml(String(item.next_run_at||'').slice(0,16))} · ${escapeHtml(status)}${item.last_error?` · ${escapeHtml(item.last_error)}`:''}</span></div><div class="mini-actions"><button data-edit-report-schedule="${item.id}">Изменить</button><button data-retry-report-schedule="${item.id}">Отправить сейчас</button><button class="danger" data-delete-report-schedule="${item.id}">Удалить</button></div>`;box.appendChild(row);});}
function applyReportSchedule(item){if(!item)return;byId('schedulePreset').value=String(item.preset_id);byId('scheduleChatId').value=String(item.delivery_chat_id);byId('scheduleFrequency').value=item.frequency||'daily';byId('scheduleHour').value=item.hour??8;byId('scheduleMinute').value=item.minute??0;byId('scheduleWeekday').value=item.weekday??0;byId('scheduleMonthDay').value=item.month_day??1;byId('scheduleTimezone').value=item.timezone_name||'server';byId('scheduleEnabled').checked=!!item.is_enabled;updateScheduleFields();showTab('reports');}
function updateScheduleFields(){const frequency=val('scheduleFrequency')||'daily';if(byId('scheduleWeekdayWrap'))byId('scheduleWeekdayWrap').style.display=frequency==='weekly'?'':'none';if(byId('scheduleMonthDayWrap'))byId('scheduleMonthDayWrap').style.display=frequency==='monthly'?'':'none';}
function renderReportDeliveryHistory(){const box=byId('reportDeliveryHistory');if(!box)return;box.innerHTML='';const list=state.report_delivery_history||[];if(!list.length){box.textContent='Отправок пока нет';box.classList.add('empty');return;}box.classList.remove('empty');list.forEach(item=>{const status={queued:'ожидает',running:'отправляется',sent:'отправлен',error:'ошибка'}[item.status]||item.status;const trigger=item.trigger_type==='manual'?'ручной повтор':'по расписанию';const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.preset_name||'Отчёт')} · ${escapeHtml(status)}</b><span>${escapeHtml(trigger)} · ${escapeHtml(String(item.created_at||'').slice(0,16))}${item.error?` · ${escapeHtml(item.error)}`:''}</span></div><div class="mini-actions"><button data-retry-report-history="${item.id}" data-schedule-id="${item.schedule_id||''}">Повторить</button></div>`;box.appendChild(row);});}
function renderInbox(){const box=byId('inboxList');if(!box)return;box.innerHTML='';const list=state.inbox_items||[];if(!list.length){box.textContent='Новых сообщений нет';box.classList.add('empty');return;}box.classList.remove('empty');list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';const mark=item.status==='unread'?'новое':item.status==='resolved'?'завершено':'прочитано';const actions=[];if(item.related_type==='inventory_session'&&item.related_id)actions.push(`<button data-open-inbox-session="${item.related_id}" data-inbox-id="${item.id}">Открыть</button>`);if(item.status==='unread')actions.push(`<button data-read-inbox="${item.id}">Прочитано</button>`);row.innerHTML=`<div><b>${escapeHtml(item.title)}</b><span>${escapeHtml(mark)} · ${escapeHtml(item.message||'')}${item.area_name?` · ${escapeHtml(item.area_name)}`:''}</span></div><div class="mini-actions">${actions.join('')}</div>`;box.appendChild(row);});}
function deviationText(value,start=true){if(value===null||value===undefined)return 'нет факта';const n=Number(value);if(Math.abs(n)<0.5)return 'по плану';if(start)return `${Math.abs(n).toLocaleString('ru-RU')} мин ${n>0?'опоздание':'раньше'}`;return `${Math.abs(n).toLocaleString('ru-RU')} мин ${n>0?'переработка':'ранний уход'}`;}
function renderShiftPlans(){const box=byId('shiftPlanList');if(box){box.innerHTML='';const list=state.shift_plans||[];if(!list.length){box.textContent='Плановых смен пока нет';box.classList.add('empty');}else{box.classList.remove('empty');list.forEach(item=>{const status={planned:'запланирована',in_progress:'идёт',completed:'завершена',cancelled:'отменена'}[item.status]||item.status;const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.worker_name)} · ${escapeHtml(status)}</b><span>${escapeHtml(String(item.planned_start||'').slice(0,16))} — ${escapeHtml(String(item.planned_end||'').slice(0,16))} · ${escapeHtml(item.area_name||'без площадки')}</span></div><div class="mini-actions">${state.can_manage&&item.status==='planned'?`<button class="danger" data-cancel-shift-plan="${item.id}">Отменить</button>`:''}</div>`;box.appendChild(row);});}}const dev=byId('attendanceDeviationList');if(dev){dev.innerHTML='';const list=state.attendance_deviations||[];if(!list.length){dev.textContent='Отклонений пока нет';dev.classList.add('empty');}else{dev.classList.remove('empty');list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.worker_name)}</b><span>${escapeHtml(String(item.planned_start||'').slice(0,16))} · начало: ${escapeHtml(deviationText(item.start_deviation_minutes,true))} · окончание: ${escapeHtml(deviationText(item.end_deviation_minutes,false))}</span></div>`;dev.appendChild(row);});}}}
function localDate(offset=0){const d=new Date();d.setDate(d.getDate()+offset);return d.toISOString().slice(0,10);}
function setStep68DateDefaults(){
  const defaults={shiftTemplateValidFrom:localDate(0),shiftTemplateAnchor:localDate(0),shiftCalendarFrom:localDate(-7),shiftCalendarTo:localDate(45),attendanceFrom:localDate(-29),attendanceTo:localDate(0)};
  Object.entries(defaults).forEach(([id,value])=>{const el=byId(id);if(el&&!el.value)el.value=value;});
  updateShiftTemplateFields();
}
function renderNotificationPreferences(){const p=state.notification_preferences||{};const checks={notifyInbox:'inbox_enabled',notifyTelegram:'telegram_enabled',notifyInventoryApproval:'inventory_approval_enabled',notifyInventoryResult:'inventory_result_enabled',notifyShiftPlan:'shift_plan_enabled',notifyApprovalReminders:'approval_reminders_enabled'};Object.entries(checks).forEach(([id,key])=>{if(byId(id))byId(id).checked=p[key]!==false;});if(byId('notifyReminderAfter'))byId('notifyReminderAfter').value=p.reminder_after_minutes??60;if(byId('notifyRepeatEvery'))byId('notifyRepeatEvery').value=p.repeat_every_minutes??120;if(byId('notifyMaxReminders'))byId('notifyMaxReminders').value=p.max_reminders??3;}
function updateShiftTemplateFields(){const cycle=val('shiftTemplatePattern')==='cycle';if(byId('shiftTemplateCycle'))byId('shiftTemplateCycle').style.display=cycle?'':'none';if(byId('shiftTemplateWeekdays'))byId('shiftTemplateWeekdays').style.display=cycle?'none':'';}
function clearShiftTemplate(){if(byId('shiftTemplateId'))byId('shiftTemplateId').value='';if(byId('shiftTemplatePattern'))byId('shiftTemplatePattern').value='weekly';document.querySelectorAll('[data-shift-weekday]').forEach(x=>x.checked=false);['shiftTemplateValidUntil','shiftTemplateNote'].forEach(id=>{if(byId(id))byId(id).value='';});if(byId('shiftTemplateWorkDays'))byId('shiftTemplateWorkDays').value=2;if(byId('shiftTemplateRestDays'))byId('shiftTemplateRestDays').value=2;setStep68DateDefaults();updateShiftTemplateFields();}
function applyShiftTemplate(item){if(!item)return;byId('shiftTemplateId').value=item.id;byId('shiftTemplateWorker').value=String(item.user_id);byId('shiftTemplateArea').value=item.area_id?String(item.area_id):'';byId('shiftTemplatePattern').value=item.pattern_type||'weekly';const days=new Set((item.weekdays||[]).map(Number));document.querySelectorAll('[data-shift-weekday]').forEach(x=>x.checked=days.has(Number(x.dataset.shiftWeekday)));byId('shiftTemplateWorkDays').value=item.cycle_work_days||2;byId('shiftTemplateRestDays').value=item.cycle_rest_days||2;byId('shiftTemplateAnchor').value=item.cycle_anchor_date||item.valid_from||localDate();byId('shiftTemplateStartTime').value=item.start_time||'09:00';byId('shiftTemplateEndTime').value=item.end_time||'18:00';byId('shiftTemplateValidFrom').value=item.valid_from||localDate();byId('shiftTemplateValidUntil').value=item.valid_until||'';byId('shiftTemplateNote').value=item.note||'';updateShiftTemplateFields();showTab('shifts');}
function renderShiftTemplates(){const box=byId('shiftTemplateList');if(!box)return;box.innerHTML='';const list=state.shift_templates||[];if(!list.length){box.textContent='Графиков пока нет';box.classList.add('empty');return;}box.classList.remove('empty');const dayNames=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];list.forEach(item=>{const pattern=item.pattern_type==='cycle'?`${item.cycle_work_days}/${item.cycle_rest_days} с ${item.cycle_anchor_date}`:(item.weekdays||[]).map(x=>dayNames[Number(x)]).join(', ');const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.worker_name)} · ${item.is_enabled?'активен':'отключён'}</b><span>${escapeHtml(pattern)} · ${escapeHtml(item.start_time)}–${escapeHtml(item.end_time)} · с ${escapeHtml(item.valid_from)}${item.valid_until?` по ${escapeHtml(item.valid_until)}`:''} · ${escapeHtml(item.area_name||'без площадки')}</span></div><div class="mini-actions"><button data-edit-shift-template="${item.id}">Изменить</button>${item.is_enabled?`<button class="danger" data-disable-shift-template="${item.id}">Отключить</button>`:''}</div>`;box.appendChild(row);});}
function renderShiftCalendar(){const box=byId('shiftCalendarList');if(!box)return;box.innerHTML='';const list=state.shift_calendar||[];if(!list.length){box.textContent='Смен в выбранном периоде нет';box.classList.add('empty');return;}box.classList.remove('empty');let current='';list.forEach(item=>{const day=String(item.planned_start||'').slice(0,10);if(day!==current){current=day;const head=document.createElement('div');head.className='row';head.innerHTML=`<b>${escapeHtml(new Date(day+'T00:00:00').toLocaleDateString('ru-RU',{weekday:'long',day:'2-digit',month:'long'}))}</b>`;box.appendChild(head);}const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(String(item.planned_start||'').slice(11,16))}–${escapeHtml(String(item.planned_end||'').slice(11,16))} · ${escapeHtml(item.worker_name)}</b><span>${escapeHtml(item.area_name||'без площадки')} · ${escapeHtml(item.status)}</span></div>`;box.appendChild(row);});}
function renderAttendanceSummary(){const box=byId('attendanceSummaryList');if(!box)return;box.innerHTML='';const list=state.attendance_summary||[];if(!list.length){box.textContent='Данных пока нет';box.classList.add('empty');return;}box.classList.remove('empty');list.forEach(item=>{const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.worker_name)}</b><span>план ${escapeHtml(item.planned_shifts)} · с фактом ${escapeHtml(item.completed_plans)} · пропусков ${escapeHtml(item.missed_shifts)} · смен ${escapeHtml(item.actual_shifts)} · ${(Number(item.worked_minutes||0)/60).toLocaleString('ru-RU',{maximumFractionDigits:1})} ч · опозданий ${escapeHtml(item.late_count)} (${escapeHtml(fmt(item.late_minutes))} мин) · ранних уходов ${escapeHtml(item.early_departure_count)} · переработка ${escapeHtml(fmt(item.overtime_minutes))} мин</span></div>`;box.appendChild(row);});}
function renderWorkerActivity(){
  const box=byId('workerActivityList');if(box){box.innerHTML='';const list=state.worker_activity||[];if(!list.length){box.textContent='Данных пока нет';box.classList.add('empty');}else{box.classList.remove('empty');list.forEach(item=>{const hours=(Number(item.shift_minutes||0)/60).toLocaleString('ru-RU',{maximumFractionDigits:1});const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.display_name)}</b><span>${escapeHtml(item.job_name||'без должности')} · операций ${escapeHtml(item.operation_count||0)} · активных дней ${escapeHtml(item.active_days||0)} · смен ${escapeHtml(item.shift_count||0)} · ${escapeHtml(hours)} ч${item.has_open_shift?' · смена открыта':''}</span></div>`;box.appendChild(row);});}}
  const shifts=byId('workerShiftList');if(shifts){shifts.innerHTML='';const list=state.worker_shifts||[];if(!list.length){shifts.textContent='Смен пока нет';shifts.classList.add('empty');}else{shifts.classList.remove('empty');list.forEach(item=>{const minutes=Number(item.duration_minutes||0);const hours=(minutes/60).toLocaleString('ru-RU',{maximumFractionDigits:1});const row=document.createElement('div');row.className='manager-row';row.innerHTML=`<div><b>${escapeHtml(item.worker_name)} · ${item.status==='open'?'идёт смена':'смена завершена'}</b><span>${escapeHtml(item.area_name||'без площадки')} · ${escapeHtml(String(item.started_at||'').slice(0,16))}${item.ended_at?` — ${escapeHtml(String(item.ended_at).slice(0,16))}`:''} · ${escapeHtml(hours)} ч${item.plan_id?` · начало: ${escapeHtml(deviationText(item.start_deviation_minutes,true))}${item.status==='closed'?` · окончание: ${escapeHtml(deviationText(item.end_deviation_minutes,false))}`:''}`:''}${item.note?` · ${escapeHtml(item.note)}`:''}</span></div>`;shifts.appendChild(row);});}}
}
function renderAccounts(){
  const box=byId('accountChooser'), list=byId('accountList'); if(!box || !list) return;
  if(!state.accounts.length || chatId){ box.classList.add('hidden'); return; }
  box.classList.remove('hidden'); list.innerHTML='';
  state.accounts.forEach(acc=>{const b=document.createElement('button'); b.className='account-btn'; b.dataset.chat=acc.scope_chat_id; b.textContent=acc.name; list.appendChild(b);});
}
async function loadAccounts(){
  if(!userId || chatId) return;
  const res=await fetch(`/api/accounts?user_id=${encodeURIComponent(userId)}`, {headers});
  if(res.ok){ const data=await res.json(); state.accounts=data.accounts||[]; renderAccounts(); }
}
async function loadAudit(){
  if(!chatId || !userId || !state.is_system_admin) return;
  const res=await fetch(`/api/audit?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}`,{headers});
  if(res.ok){state.audit=await res.json(); renderAudit();}
  const sec=await fetch(`/api/security-status?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}`,{headers});
  if(sec.ok){state.security=await sec.json(); renderSecurity();}
}

function departmentChoices(){return (state.departments||[]).map(x=>({id:x.id,name:x.name}));}
function renderDepartmentSelects(){
  const choices=departmentChoices();
  ['departmentOperationDepartment','departmentEntityDepartment','departmentMemberDepartment'].forEach(id=>fillSelect(id,choices,'Выберите отдел'));
  const ops=departmentOperationOptions.map(id=>({id,name:operationLabels[id]||id}));
  fillSelect('departmentOperationKey',ops,'Выберите действие');
  fillSelect('departmentEntityOperation',ops,'Выберите действие');
  updateDepartmentEntityChoices();
  renderDepartmentMemberOperationChecks();
}
function updateDepartmentEntityChoices(){const type=val('departmentEntityType')||'component';fillSelect('departmentEntityId',(state.entities?.[type]||[]).map(x=>({id:x.id,name:x.name})),'Выберите позицию');}
function selectedDepartment(){const id=val('departmentMemberDepartment');return (state.departments||[]).find(x=>String(x.id)===String(id));}
function renderDepartmentMemberOperationChecks(){const box=byId('departmentMemberOperations');if(!box)return;const dep=selectedDepartment();const operations=(dep?.operations||[]).filter(x=>x.can_view||x.can_submit||x.can_edit);box.innerHTML='';operations.forEach(item=>{const label=document.createElement('label');label.className='check-line';label.innerHTML=`<input type="checkbox" data-department-member-operation="${item.operation_key}" /> ${operationLabels[item.operation_key]||item.operation_key}`;box.appendChild(label);});if(!operations.length)box.innerHTML='<span class="muted">Сначала добавьте действия отделу.</span>';}
function renderDepartments(){
  const box=byId('departmentList');if(!box)return;box.innerHTML='';const list=state.departments||[];
  if(!list.length){box.textContent='Отделы ещё не созданы';box.classList.add('empty');return;}box.classList.remove('empty');
  list.forEach(dep=>{const row=document.createElement('div');row.className='manager-row';const ops=(dep.operations||[]).map(x=>operationLabels[x.operation_key]||x.operation_key).join(', ')||'действия не выбраны';const positions=(dep.entities||[]).map(x=>`${operationLabels[x.operation_key]||x.operation_key}: ${x.entity_name}`).join(', ')||'позиции не назначены';const members=(dep.members||[]).map(x=>`${x.display_name||x.user_id} — ${departmentRoleLabels[x.role_level]||x.role_name}`).join('; ')||'сотрудники не добавлены';let actions='';if(state.is_system_admin)actions+=`<button data-edit-department="${dep.id}">Изменить</button><button class="danger" data-delete-department="${dep.id}">Отключить</button>`;(dep.operations||[]).forEach(x=>{if(state.is_system_admin)actions+=`<button data-delete-department-operation="${dep.id}:${x.operation_key}">Убрать ${operationLabels[x.operation_key]||x.operation_key}</button>`});(dep.entities||[]).forEach(x=>{if(state.is_system_admin)actions+=`<button data-delete-department-entity="${dep.id}:${x.operation_key}:${x.entity_id}">Убрать ${x.entity_name}</button>`});(dep.members||[]).forEach(x=>{actions+=`<button data-edit-department-member="${dep.id}:${x.user_id}">Изменить ${x.display_name||x.user_id}</button><button class="danger" data-delete-department-member="${dep.id}:${x.user_id}">Отключить доступ</button>`});row.innerHTML=`<div><b>${dep.name}</b><small>${dep.description||''}</small><small><b>Действия:</b> ${ops}</small><small><b>Позиции:</b> ${positions}</small><small><b>Люди:</b> ${members}</small></div><div class="manager-actions">${actions}</div>`;box.appendChild(row);});
}
function clearDepartment(){byId('departmentId').value='';byId('departmentName').value='';byId('departmentDescription').value='';}
async function saveDepartment(){const name=val('departmentName');if(!name){showNotice('Укажите название отдела.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),department_id:val('departmentId')?Number(val('departmentId')):null,name,description:val('departmentDescription')};const res=await fetch('/api/departments',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Отдел не сохранён.',true);return;}state.departments=data.departments||[];clearDepartment();renderDepartmentSelects();renderDepartments();showNotice(data.message||'Отдел сохранён.');}
async function deleteDepartment(id){const res=await fetch(`/api/departments?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&department_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Отдел не отключён.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Отдел отключён.');}
async function saveDepartmentOperation(){const department=val('departmentOperationDepartment'),operation=val('departmentOperationKey');if(!department||!operation){showNotice('Выберите отдел и действие.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),department_id:Number(department),operation_key:operation,can_view:byId('departmentOperationView').checked,can_submit:byId('departmentOperationSubmit').checked,can_edit:byId('departmentOperationEdit').checked};const res=await fetch('/api/departments/operation',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Действие не сохранено.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Действие сохранено.');}
async function deleteDepartmentOperation(raw){const [department,operation]=raw.split(':');const q=new URLSearchParams({chat_id:chatId,user_id:userId,department_id:department,operation_key:operation});const res=await fetch('/api/departments/operation?'+q.toString(),{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Действие не удалено.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Действие удалено.');}
async function saveDepartmentEntity(){const department=val('departmentEntityDepartment'),operation=val('departmentEntityOperation'),type=val('departmentEntityType'),entity=val('departmentEntityId');if(!department||!operation||!entity){showNotice('Выберите отдел, действие и позицию.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),department_id:Number(department),operation_key:operation,entity_type:type,entity_id:Number(entity),can_view:true,can_submit:true};const res=await fetch('/api/departments/entity',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Позиция не назначена.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Позиция назначена.');}
async function deleteDepartmentEntity(raw){const [department,operation,entity]=raw.split(':');const q=new URLSearchParams({chat_id:chatId,user_id:userId,department_id:department,operation_key:operation,entity_id:entity});const res=await fetch('/api/departments/entity?'+q.toString(),{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Позиция не удалена.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Позиция удалена.');}
async function saveDepartmentMember(){const department=val('departmentMemberDepartment'),member=val('departmentMemberUserId');if(!department||!member){showNotice('Выберите отдел и укажите Telegram ID.',true);return;}const operation_keys=[...document.querySelectorAll('[data-department-member-operation]:checked')].map(x=>x.dataset.departmentMemberOperation);const body={chat_id:Number(chatId),user_id:Number(userId),department_id:Number(department),member_user_id:Number(member),display_name:val('departmentMemberName'),role_level:Number(val('departmentMemberRole')||20),operation_keys};const res=await fetch('/api/departments/member',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Доступ не сохранён.',true);return;}state.departments=data.departments||[];byId('departmentMemberUserId').value='';byId('departmentMemberName').value='';renderDepartmentSelects();renderDepartments();showNotice(data.message||'Доступ сохранён.');}
async function deleteDepartmentMember(raw){const [department,member]=raw.split(':');const q=new URLSearchParams({chat_id:chatId,user_id:userId,department_id:department,member_user_id:member});const res=await fetch('/api/departments/member?'+q.toString(),{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Доступ не отключён.',true);return;}state.departments=data.departments||[];renderDepartmentSelects();renderDepartments();showNotice(data.message||'Доступ отключён.');}


function riskAllEntities(){return [...entity('material'),...entity('component'),...entity('product'),...entity('stock_item')];}
function updateRiskObservationEntities(){const t=val('riskObservationType')||'material';fillSelect('riskObservationEntity',entity(t),'Позиция');const mode=val('riskObservationMode')||'balance';const period=byId('riskObservationPeriod');if(period){period.disabled=mode==='balance';if(mode==='balance')period.value='instant';}}
function updateRiskRuleEntities(){const t=val('stockRuleType')||'material';fillSelect('stockRuleEntity',entity(t),'Позиция');}
function riskManagerDepartmentIds(){return new Set((state.department_memberships||[]).filter(x=>Number(x.role_level||0)>=50).map(x=>Number(x.department_id)));}
function canManageRiskImpact(){return !!state.is_system_admin || riskManagerDepartmentIds().size>0;}
function canResolveRiskEvent(x){if(state.is_system_admin)return true;if(Number(x.created_by||0)===Number(userId||0))return true;const heads=riskManagerDepartmentIds();return !!x.department_id&&heads.has(Number(x.department_id));}
function applyRiskEventAccess(){const allowed=canManageRiskImpact();['riskEventImpact','riskEventImpactValue','riskEventUnavailable'].forEach(id=>{const el=byId(id);if(el){el.disabled=!allowed;el.title=allowed?'':'Сотрудник сообщает событие; влияние на расчёт подтверждает руководитель.';}});const note=byId('riskEventAccessNote');if(note)note.textContent=allowed?'Можно задать влияние события на прогноз.':'Сообщение будет отправлено ответственным. Числовое влияние подтвердит руководитель.';}
function updateRiskEventEntities(){fillSelect('riskEventEntity',riskAllEntities(),'Без конкретной позиции');const type=val('riskEventType');const item=(state.stock_risk?.event_catalog||[]).find(x=>x.key===type);if(item&&!val('riskEventTitle'))byId('riskEventTitle').value=item.label||'';if(item&&canManageRiskImpact())byId('riskEventImpact').value=item.impact_kind||'info';applyRiskEventAccess();}
function riskSeverityLabel(v){return ({emergency:'🚨 Авария',critical:'🔴 Критично',warning:'⚠️ Предупреждение',unknown:'⚪ Нет нормы',ok:'✅ Норма'})[v]||v;}
function renderStockRisk(){
  const data=state.stock_risk||{}, rules=data.rules||[], incidents=data.incidents||[], events=data.events||[], observations=data.observations||[], summary=data.summary||{};
  renderRows(byId('stockRiskSummary'),[
    {name:'Аварийные',value:String(summary.emergency||0),flag:(summary.emergency||0)?'flag-red':''},
    {name:'Критические',value:String(summary.critical||0),flag:(summary.critical||0)?'flag-red':''},
    {name:'Предупреждения',value:String(summary.warning||0)},
    {name:'Без нормы расхода',value:String(summary.unknown||0)}
  ],'Правила ещё не настроены');
  const incidentBox=byId('stockIncidentList');if(incidentBox){incidentBox.innerHTML='';if(!incidents.length){incidentBox.textContent='Активных тревог нет';incidentBox.classList.add('empty');}else{incidentBox.classList.remove('empty');incidents.forEach(x=>{const row=document.createElement('div');row.className='manager-row '+(x.severity==='emergency'||x.severity==='critical'?'flag-red':'');const reserve=x.reserve_shifts==null?'не рассчитан':`${Number(x.reserve_shifts).toFixed(1)} смен`;row.innerHTML=`<div><b>${escapeHtml(riskSeverityLabel(x.severity))} · ${escapeHtml(x.entity_name||'Позиция')}</b><small>${escapeHtml(x.area_name||'Все площадки')} · запас ${escapeHtml(reserve)}</small><small>${escapeHtml(x.message||'')}</small></div><div class="mini-actions"><button data-ack-risk="${x.id}:0">Принято</button><button data-ack-risk="${x.id}:60">Отложить на час</button></div>`;incidentBox.appendChild(row);});}}
  const eventBox=byId('riskEventList');if(eventBox){eventBox.innerHTML='';if(!events.length){eventBox.textContent='Активных событий нет';eventBox.classList.add('empty');}else{eventBox.classList.remove('empty');events.forEach(x=>{const row=document.createElement('div');row.className='manager-row '+(x.severity==='emergency'||x.severity==='critical'?'flag-red':'');const actions=canResolveRiskEvent(x)?`<button data-resolve-risk-event="${x.id}">Устранено</button>`:'';row.innerHTML=`<div><b>${escapeHtml(riskSeverityLabel(x.severity))} · ${escapeHtml(x.title)}</b><small>${escapeHtml([x.area_name,x.department_name,x.entity_name].filter(Boolean).join(' · ')||'Общее событие')}</small><small>${escapeHtml(x.note||'')}</small></div><div class="mini-actions">${actions}</div>`;eventBox.appendChild(row);});}}
  const ruleBox=byId('stockRuleList');if(ruleBox){ruleBox.innerHTML='';if(!rules.length){ruleBox.textContent='Правила ещё не настроены';ruleBox.classList.add('empty');}else{ruleBox.classList.remove('empty');rules.forEach(x=>{const row=document.createElement('div');row.className='manager-row '+(x.severity==='emergency'||x.severity==='critical'?'flag-red':'');const reserve=x.reserve_shifts==null?'не рассчитан':`${Number(x.reserve_shifts).toFixed(1)} смен`;const buttons=state.is_system_admin?`<button data-edit-stock-rule="${x.id}">Изменить</button><button class="danger" data-delete-stock-rule="${x.id}">Удалить</button>`:'';row.innerHTML=`<div><b>${escapeHtml(x.entity_name)} · ${escapeHtml(riskSeverityLabel(x.severity||'unknown'))}</b><small>${escapeHtml(x.area_name||'Все площадки')} · остаток ${fmt(x.stock_quantity)} ${escapeHtml(x.default_unit||'')} · ${escapeHtml(reserve)}</small><small>Пороги: ${fmt(x.warning_shifts)} / ${fmt(x.critical_shifts)} / ${fmt(x.emergency_shifts)} смен</small></div><div class="mini-actions">${buttons}</div>`;ruleBox.appendChild(row);});}}
  renderRows(byId('stockObservationList'),observations.slice(0,60).map(x=>({name:`${x.entity_name}${x.area_name?` · ${x.area_name}`:''}`,value:`${x.observation_type==='balance'?'остаток':'расход'} ${fmt(x.quantity)} ${x.unit} · ${x.period_kind} × ${fmt(x.period_count)} · ${String(x.created_at||'').slice(0,16)}`})),'Записей пока нет');
}
async function refreshStockRisk(){const res=await fetch(`/api/stock-risks?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}`,{headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Риски недоступны.',true);return;}state.stock_risk=data;renderStockRisk();}
async function saveStockObservation(){const type=val('riskObservationType'),entityId=val('riskObservationEntity'),amount=qty('riskObservationQuantity');if(!entityId||Number.isNaN(amount)||amount<0){showNotice('Выберите позицию и укажите количество.',true);return;}const selected=entity(type).find(x=>String(x.id)===String(entityId));const body={chat_id:Number(chatId),user_id:Number(userId),mode:val('riskObservationMode'),entity_type:type,entity_id:Number(entityId),area_id:val('riskObservationArea')?Number(val('riskObservationArea')):null,quantity:amount,unit:selected?.unit||'',period_kind:val('riskObservationPeriod')||'instant',period_count:Math.max(0.01,qty('riskObservationPeriodCount')||1),note:val('riskObservationNote')};const res=await fetch('/api/stock-observations',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Данные не сохранены.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;state.inventory_positions=data.inventory_positions||state.inventory_positions;byId('riskObservationQuantity').value='';byId('riskObservationNote').value='';renderStockRisk();renderInventoryPositions();showNotice('Фактические данные сохранены.');}
async function saveRiskEvent(){const type=val('riskEventType');if(!type){showNotice('Выберите тип события.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),event_type:type,title:val('riskEventTitle'),area_id:val('riskEventArea')?Number(val('riskEventArea')):null,department_id:val('riskEventDepartment')?Number(val('riskEventDepartment')):null,entity_id:val('riskEventEntity')?Number(val('riskEventEntity')):null,severity:val('riskEventSeverity'),impact_kind:val('riskEventImpact'),impact_value:qty('riskEventImpactValue')||0,unavailable_quantity:qty('riskEventUnavailable')||0,starts_at:val('riskEventStart')?val('riskEventStart').replace('T',' ')+':00':'',ends_at:val('riskEventEnd')?val('riskEventEnd').replace('T',' ')+':00':null,note:val('riskEventNote')};const res=await fetch('/api/operational-events',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Событие не сохранено.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;byId('riskEventNote').value='';byId('riskEventImpactValue').value='0';byId('riskEventUnavailable').value='0';renderStockRisk();showNotice('Событие зарегистрировано, ответственные уведомлены.');}
async function resolveRiskEvent(id){const body={chat_id:Number(chatId),user_id:Number(userId),event_id:Number(id),event_type:'force_majeure',title:'',severity:'warning',impact_kind:'info',impact_value:0,unavailable_quantity:0,starts_at:'',note:''};const res=await fetch('/api/operational-events/resolve',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Событие не закрыто.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;renderStockRisk();showNotice('Событие закрыто.');}
async function acknowledgeRisk(raw){const [id,minutes]=String(raw).split(':');const res=await fetch('/api/stock-incidents/acknowledge',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),incident_id:Number(id),snooze_minutes:Number(minutes||0)})});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Тревога не отмечена.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;renderStockRisk();showNotice(minutes==='0'?'Принято.':'Тревога отложена.');}
function clearStockRule(){['stockRuleId','stockRuleNotifyIds','stockRuleAbsoluteWarning','stockRuleAbsoluteCritical','stockRulePlannedEntity'].forEach(id=>{if(byId(id))byId(id).value='';});[['stockRuleConsumption','0'],['stockRuleShiftsDay','1'],['stockRuleWorkDays','5'],['stockRuleWarning','10'],['stockRuleCritical','5'],['stockRuleEmergency','1'],['stockRuleBuffer','0'],['stockRuleDemandMultiplier','1'],['stockRuleWindow','28'],['stockRuleMinSamples','2'],['stockRuleStale','168'],['stockRuleAnomaly','2'],['stockRuleRepeat','180'],['stockRuleYieldInput','0'],['stockRuleYieldOutput','0'],['stockRulePlannedQty','0']].forEach(([id,v])=>{if(byId(id))byId(id).value=v;});if(byId('stockRulePlannedPeriod'))byId('stockRulePlannedPeriod').value='shift';['stockRuleEnabled','stockRuleNotifyOwner','stockRuleNotifyAdmins','stockRuleNotifyHeads','stockRuleStaleAlert','stockRuleNegativeAlert','stockRuleAnomalyAlert'].forEach(id=>{if(byId(id))byId(id).checked=true;});if(byId('stockRuleNotifyWorkChat'))byId('stockRuleNotifyWorkChat').checked=false;}
function applyStockRule(x){if(!x)return;byId('stockRuleId').value=x.id;byId('stockRuleType').value=x.entity_type;updateRiskRuleEntities();byId('stockRuleEntity').value=x.entity_id;byId('stockRuleArea').value=x.area_id||'';byId('stockRuleMode').value=x.calculation_mode||'hybrid';byId('stockRuleConsumption').value=x.manual_consumption_qty||0;byId('stockRulePeriod').value=x.manual_period||'shift';byId('stockRuleShiftsDay').value=x.shifts_per_day||1;byId('stockRuleWorkDays').value=x.work_days_per_week||5;byId('stockRuleWarning').value=x.warning_shifts||10;byId('stockRuleCritical').value=x.critical_shifts||5;byId('stockRuleEmergency').value=x.emergency_shifts||1;byId('stockRuleBuffer').value=x.safety_buffer_qty||0;byId('stockRuleAbsoluteWarning').value=x.absolute_warning_qty??'';byId('stockRuleAbsoluteCritical').value=x.absolute_critical_qty??'';byId('stockRuleDemandMultiplier').value=x.demand_multiplier||1;byId('stockRuleWindow').value=x.learning_window_days||28;byId('stockRuleMinSamples').value=x.minimum_samples||2;byId('stockRuleStale').value=x.stale_after_hours||168;byId('stockRuleAnomaly').value=x.anomaly_multiplier||2;byId('stockRuleRepeat').value=x.repeat_minutes||180;byId('stockRuleYieldInput').value=x.yield_input_qty||0;byId('stockRuleYieldOutput').value=x.yield_output_qty||0;byId('stockRuleYieldEntity').value=x.yield_output_entity_id||'';byId('stockRulePlannedEntity').value=x.planned_output_entity_id||'';byId('stockRulePlannedQty').value=x.planned_output_qty||0;byId('stockRulePlannedPeriod').value=x.planned_output_period||'shift';byId('stockRuleNotifyIds').value=(x.notify_user_ids||[]).join(', ');byId('stockRuleEnabled').checked=!!x.is_enabled;byId('stockRuleNotifyOwner').checked=!!x.notify_owner;byId('stockRuleNotifyAdmins').checked=!!x.notify_system_admins;byId('stockRuleNotifyHeads').checked=!!x.notify_department_heads;byId('stockRuleNotifyWorkChat').checked=!!x.notify_work_chat;byId('stockRuleStaleAlert').checked=!!x.alert_on_stale;byId('stockRuleNegativeAlert').checked=!!x.alert_on_negative;byId('stockRuleAnomalyAlert').checked=!!x.alert_on_anomaly;showTab('risks');}
async function saveStockRule(){const type=val('stockRuleType'),entityId=val('stockRuleEntity');if(!entityId){showNotice('Выберите позицию.',true);return;}const notifyIds=String(val('stockRuleNotifyIds')).split(/[;,\s]+/).map(Number).filter(x=>x>0);const body={chat_id:Number(chatId),user_id:Number(userId),rule_id:val('stockRuleId')?Number(val('stockRuleId')):null,entity_type:type,entity_id:Number(entityId),area_id:val('stockRuleArea')?Number(val('stockRuleArea')):null,is_enabled:byId('stockRuleEnabled').checked,calculation_mode:val('stockRuleMode'),manual_consumption_qty:qty('stockRuleConsumption')||0,manual_period:val('stockRulePeriod'),shifts_per_day:qty('stockRuleShiftsDay')||1,work_days_per_week:qty('stockRuleWorkDays')||5,warning_shifts:qty('stockRuleWarning'),critical_shifts:qty('stockRuleCritical'),emergency_shifts:qty('stockRuleEmergency'),absolute_warning_qty:val('stockRuleAbsoluteWarning')===''?null:qty('stockRuleAbsoluteWarning'),absolute_critical_qty:val('stockRuleAbsoluteCritical')===''?null:qty('stockRuleAbsoluteCritical'),safety_buffer_qty:qty('stockRuleBuffer')||0,learning_window_days:Number(val('stockRuleWindow')||28),minimum_samples:Number(val('stockRuleMinSamples')||2),stale_after_hours:Number(val('stockRuleStale')||168),anomaly_multiplier:qty('stockRuleAnomaly')||2,demand_multiplier:qty('stockRuleDemandMultiplier')||1,yield_output_entity_id:val('stockRuleYieldEntity')?Number(val('stockRuleYieldEntity')):null,yield_input_qty:qty('stockRuleYieldInput')||0,yield_output_qty:qty('stockRuleYieldOutput')||0,planned_output_entity_id:val('stockRulePlannedEntity')?Number(val('stockRulePlannedEntity')):null,planned_output_qty:qty('stockRulePlannedQty')||0,planned_output_period:val('stockRulePlannedPeriod')||'shift',notify_owner:byId('stockRuleNotifyOwner').checked,notify_system_admins:byId('stockRuleNotifyAdmins').checked,notify_department_heads:byId('stockRuleNotifyHeads').checked,notify_work_chat:byId('stockRuleNotifyWorkChat').checked,notify_user_ids:notifyIds,repeat_minutes:Number(val('stockRuleRepeat')||180),alert_on_stale:byId('stockRuleStaleAlert').checked,alert_on_negative:byId('stockRuleNegativeAlert').checked,alert_on_anomaly:byId('stockRuleAnomalyAlert').checked};const res=await fetch('/api/stock-alert-rules',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Правило не сохранено.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;clearStockRule();renderStockRisk();showNotice(data.message||'Правило сохранено.');}
async function deleteStockRule(id){const q=new URLSearchParams({chat_id:chatId,user_id:userId,rule_id:id});const res=await fetch('/api/stock-alert-rules?'+q.toString(),{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Правило не удалено.',true);return;}state.stock_risk=data.stock_risk||state.stock_risk;renderStockRisk();showNotice('Правило удалено.');}

async function load(){
  if(!userId || !initData){ renderRows(byId('alerts'), [{name:'Доступ закрыт', value:'откройте Mini App кнопкой внутри Telegram-бота'}]); return; }
  if(!chatId){ await loadAccounts(); if(!state.accounts.length) renderRows(byId('alerts'), [{name:'Учёт не выбран', value:'откройте группу в боте'}]); return; }
  const res = await fetch(`/api/bootstrap?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}`, {headers});
  if(!res.ok){ renderRows(byId('alerts'), [{name:'Нет доступа', value:'проверьте выбранный учёт'}]); return; }
  const data = await res.json(); state = {...state, ...data}; byId('accountName').textContent = data.account?.name || 'Учёт'; fillForms(); renderDashboard(data.dashboard||{}); applyAccess(); await loadAudit(); renderAccounts();
  const savedTab = localStorage.getItem('prodMiniTab'); if(savedTab) showTab(savedTab);
}
function val(id){return byId(id)?.value || '';}
function qty(id){return Number(String(val(id)).replace(',','.').replace(/\s+/g,''));}
async function postOperation(kind, etype, eid, amount, areaId, extra={}){
  if(!eid || !amount || amount<=0){showNotice('Укажите позицию и количество.', true); return;}
  const body={chat_id:Number(chatId), user_id:Number(userId), operation_type:kind, entity_type:etype, entity_id:Number(eid), quantity:amount, area_id: areaId?Number(areaId):null, ...extra};
  const res=await fetch('/api/operations',{method:'POST',headers,body:JSON.stringify(body)});
  if(!res.ok){const err=await res.json().catch(()=>({}));showNotice(err.detail==='area access denied'?'Нет доступа к выбранной площадке.':'Запись не сохранена.', true); return;}
  const data=await res.json(); state.dashboard=data.dashboard; renderDashboard(data.dashboard); await loadAudit(); showNotice('Сохранено.');
}
function parseTargets(){return String(val('planTargets')).split(/[;,\s]+/).map(x=>Number(x.replace(',','.'))).filter(x=>x>0);}
async function savePlan(){
  const product=val('planProduct'); const targets=parseTargets();
  if(!product || !targets.length){showNotice('Выберите изделие и количество.', true);return;}
  const res=await fetch('/api/plans',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),product_id:Number(product),targets})});
  if(!res.ok){showNotice('План не сохранён.', true);return;} const data=await res.json(); state.plan_targets=data.targets; renderPlan(); showNotice('План сохранён.');
}
async function clearPlan(){
  const product=val('planProduct'); const tail=product?`&product_id=${encodeURIComponent(product)}`:'';
  const res=await fetch(`/api/plans?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}${tail}`,{method:'DELETE',headers});
  if(!res.ok){showNotice('План не очищен.', true);return;} const data=await res.json(); state.plan_targets=data.targets; renderPlan(); showNotice('План очищен.');
}
async function downloadReport(format){
  const area=val('reportArea');
  const res=await fetch('/api/report',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),request_text:val('reportText')||'отчёт за месяц',format,area_id:area?Number(area):null})});
  if(!res.ok){showNotice('Отчёт не собран.', true);return;} await downloadBlob(res, format==='pdf'?'report.pdf':'report.xlsx');
}
async function saveDestination(){
  const name=val('destinationName').trim(); if(!name){showNotice('Укажите название места.',true);return;}
  const body={chat_id:Number(chatId),user_id:Number(userId),destination_id:val('destinationId')?Number(val('destinationId')):null,name,destination_type:val('destinationType')||'storage'};
  const res=await fetch('/api/destinations',{method:'POST',headers,body:JSON.stringify(body)});
  if(!res.ok){showNotice('Место не сохранено.',true);return;}const data=await res.json();state.destinations=data.destinations||[];clearDestination();fillForms();showNotice(data.message||'Сохранено.');
}
function clearDestination(){if(byId('destinationId'))byId('destinationId').value='';if(byId('destinationName'))byId('destinationName').value='';if(byId('destinationType'))byId('destinationType').value='storage';}
async function deleteDestination(id){
  const res=await fetch(`/api/destinations?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&destination_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});
  if(!res.ok){showNotice('Место не удалено.',true);return;}const data=await res.json();state.destinations=data.destinations||[];fillForms();showNotice('Место удалено.');
}
async function saveAreaAccess(){
  const job=val('accessJob'),area=val('accessArea'),section=val('accessSection');if(!job||!area||!section){showNotice('Выберите должность, площадку и раздел.',true);return;}
  const body={chat_id:Number(chatId),user_id:Number(userId),job_title_id:Number(job),area_id:Number(area),section_key:section,can_view:!!byId('accessView')?.checked,can_submit:!!byId('accessSubmit')?.checked,can_edit:!!byId('accessEdit')?.checked};
  const res=await fetch('/api/area-access',{method:'POST',headers,body:JSON.stringify(body)});if(!res.ok){showNotice('Правило не сохранено.',true);return;}const data=await res.json();state.area_access_rules=data.rules||[];renderAreaAccess();showNotice('Правило сохранено.');
}
async function deleteAreaRule(key){
  const [job,area,section]=String(key).split(':');const res=await fetch(`/api/area-access?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&job_title_id=${encodeURIComponent(job)}&area_id=${encodeURIComponent(area)}&section_key=${encodeURIComponent(section)}`,{method:'DELETE',headers});if(!res.ok){showNotice('Правило не удалено.',true);return;}const data=await res.json();state.area_access_rules=data.rules||[];renderAreaAccess();showNotice('Правило удалено.');
}
async function saveJobTitle(){
  const name=val('jobTitleName').trim();if(!name){showNotice('Укажите название должности.',true);return;}
  const body={chat_id:Number(chatId),user_id:Number(userId),job_title_id:val('jobTitleId')?Number(val('jobTitleId')):null,name,permissions:currentJobPermissions()};
  const res=await fetch('/api/job-titles',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Должность не сохранена.',true);return;}state.job_titles=data.job_titles||[];clearJobTitle();fillForms();showNotice(data.message||'Должность сохранена.');
}
async function deleteJobTitle(id){
  const res=await fetch(`/api/job-titles?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&job_title_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Должность не удалена.',true);return;}state.job_titles=data.job_titles||[];state.area_access_rules=data.area_access_rules||state.area_access_rules;fillForms();showNotice(data.message||'Должность удалена.');
}
async function saveWorker(){
  const workerId=Number(val('workerUserId'));const job=Number(val('workerJob'));if(!workerId||!job){showNotice('Укажите Telegram ID и должность.',true);return;}
  const body={chat_id:Number(chatId),user_id:Number(userId),worker_user_id:workerId,display_name:val('workerName').trim(),job_title_id:job};
  const res=await fetch('/api/workers',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Сотрудник не сохранён.',true);return;}state.workers=data.workers||[];clearWorker();renderTeam();showNotice(data.message||'Сотрудник сохранён.');
}
async function deleteWorker(id){
  const res=await fetch(`/api/workers?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&worker_user_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Доступ не отключён.',true);return;}state.workers=data.workers||[];renderTeam();showNotice(data.message||'Доступ отключён.');
}
async function loadInventoryHistory(){
  const area=val('inventoryArea'),type=val('inventoryType'),entityId=val('inventoryEntity');
  if(!area||!type||!entityId){state.inventory_history=[];renderInventoryHistory();return;}
  const res=await fetch(`/api/inventory-history?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&area_id=${encodeURIComponent(area)}&entity_type=${encodeURIComponent(type)}&entity_id=${encodeURIComponent(entityId)}&limit=80`,{headers});
  if(!res.ok){showNotice('Журнал остатков недоступен.',true);return;}const data=await res.json();state.inventory_history=data.history||[];renderInventoryHistory();
}
async function saveInventoryCorrection(){
  const area=val('inventoryArea'),type=val('inventoryType'),entityId=val('inventoryEntity'),actual=qty('inventoryActual');
  if(!area||!type||!entityId||Number.isNaN(actual)||actual<0){showNotice('Выберите позицию и укажите фактический остаток.',true);return;}
  const selected=entity(type).find(x=>String(x.id)===String(entityId));const stockRow=selectedInventoryPosition();
  const body={chat_id:Number(chatId),user_id:Number(userId),area_id:Number(area),entity_type:type,entity_id:Number(entityId),actual_quantity:actual,unit:stockRow?.unit||selected?.unit||'',note:val('inventoryNote')};
  const res=await fetch('/api/inventory-correction',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail==='area access denied'?'Нет права корректировать эту площадку.':'Корректировка не сохранена.',true);return;}state.inventory_positions=data.inventory_positions||[];state.inventory_history=data.history||[];state.dashboard=data.dashboard||state.dashboard;renderInventoryPositions();renderInventoryHistory();renderDashboard(state.dashboard||{});updateInventoryCurrent();byId('inventoryActual').value='';byId('inventoryNote').value='';showNotice(Math.abs(Number(data.delta||0))<1e-9?'Остаток уже совпадает.':'Остаток скорректирован.');
}
async function saveReportPreset(){
  const name=val('reportPresetName').trim();if(!name){showNotice('Укажите название шаблона.',true);return;}
  const area=val('reportArea');const body={chat_id:Number(chatId),user_id:Number(userId),preset_id:val('reportPresetId')?Number(val('reportPresetId')):null,name,request_text:val('reportText')||'отчёт за месяц',format:val('reportPresetFormat')||'xlsx',area_id:area?Number(area):null};
  const res=await fetch('/api/report-presets',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Шаблон не сохранён.',true);return;}state.report_presets=data.presets||[];clearReportPreset();renderReportPresets();showNotice(data.message||'Шаблон сохранён.');
}
async function deleteReportPreset(id){
  const res=await fetch(`/api/report-presets?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&preset_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});if(!res.ok){showNotice('Шаблон не удалён.',true);return;}const data=await res.json();state.report_presets=data.presets||[];renderReportPresets();showNotice('Шаблон удалён.');
}

async function createInventorySession(){const area=val('inventorySessionArea');if(!area){showNotice('Выберите площадку.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),area_id:Number(area),note:val('inventorySessionNote')};const res=await fetch('/api/inventory-sessions',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Пересчёт не создан.',true);return;}state.inventory_sessions=data.sessions||[];state.active_inventory_session=data.session||null;if(state.active_inventory_session)byId('inventorySessionId').value=state.active_inventory_session.id;renderInventorySessions();showNotice(data.message||'Пересчёт создан.');}
async function addInventorySessionItem(){const sessionId=val('inventorySessionId'),type=val('inventorySessionType'),entityId=val('inventorySessionEntity'),actual=qty('inventorySessionActual');if(!sessionId||!entityId||Number.isNaN(actual)||actual<0){showNotice('Откройте пересчёт и укажите фактическое количество.',true);return;}const selected=entity(type).find(x=>String(x.id)===String(entityId));const body={chat_id:Number(chatId),user_id:Number(userId),session_id:Number(sessionId),entity_type:type,entity_id:Number(entityId),actual_quantity:actual,unit:selected?.unit||'',note:val('inventorySessionItemNote')};const res=await fetch('/api/inventory-session-items',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Позиция не добавлена.',true);return;}state.inventory_sessions=data.sessions||[];state.active_inventory_session=data.session||null;byId('inventorySessionActual').value='';byId('inventorySessionItemNote').value='';renderInventorySessions();showNotice(data.message||'Позиция добавлена.');}
async function deleteInventorySessionItem(id){const sessionId=val('inventorySessionId');const res=await fetch(`/api/inventory-session-items?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}&item_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice('Позиция не удалена.',true);return;}state.inventory_sessions=data.sessions||[];state.active_inventory_session=data.session||null;renderInventorySessions();}
async function inventorySessionAction(action){const sessionId=val('inventorySessionId');if(!sessionId){showNotice('Выберите сессию инвентаризации.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),session_id:Number(sessionId),action,note:val('inventorySessionNote')};const res=await fetch('/api/inventory-session-action',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Статус не изменён.',true);return;}state.inventory_sessions=data.sessions||[];state.active_inventory_session=data.session||null;state.inventory_positions=data.inventory_positions||state.inventory_positions;state.dashboard=data.dashboard||state.dashboard;state.inbox_items=data.inbox_items||state.inbox_items;renderInventorySessions();renderInventoryPositions();renderDashboard(state.dashboard||{});renderInbox();showNotice(data.message||'Сохранено.');}
async function saveReportSchedule(){const preset=val('schedulePreset'),delivery=Number(val('scheduleChatId')),frequency=val('scheduleFrequency');if(!preset||!delivery){showNotice('Выберите шаблон и укажите Telegram ID.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),preset_id:Number(preset),delivery_chat_id:delivery,frequency,hour:Number(val('scheduleHour')||0),minute:Number(val('scheduleMinute')||0),weekday:Number(val('scheduleWeekday')||0),month_day:Number(val('scheduleMonthDay')||1),enabled:!!byId('scheduleEnabled')?.checked,timezone_name:val('scheduleTimezone')||'server'};const res=await fetch('/api/report-schedules',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Расписание не сохранено.',true);return;}state.report_schedules=data.schedules||[];renderReportSchedules();showNotice(data.message||'Расписание сохранено.');}
async function deleteReportSchedule(id){const res=await fetch(`/api/report-schedules?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&schedule_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice('Расписание не удалено.',true);return;}state.report_schedules=data.schedules||[];state.report_delivery_history=data.delivery_history||state.report_delivery_history;renderReportSchedules();renderReportDeliveryHistory();showNotice('Расписание удалено.');}
async function shiftAction(action){const target=Number(val('shiftWorker')||userId);const area=val('shiftArea');const body={chat_id:Number(chatId),user_id:Number(userId),worker_user_id:target,area_id:area?Number(area):null,note:val('shiftNote')};const res=await fetch(`/api/shifts/${action}`,{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Смена не изменена.',true);return;}state.worker_activity=data.activity||[];state.worker_shifts=data.shifts||[];state.shift_plans=data.shift_plans||state.shift_plans;state.attendance_deviations=data.attendance_deviations||state.attendance_deviations;byId('shiftNote').value='';renderWorkerActivity();renderShiftPlans();showNotice(data.message||'Смена сохранена.');}
async function loadWorkerActivity(){const days=Number(val('activityDays')||30);const res=await fetch(`/api/worker-activity?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&days=${encodeURIComponent(days)}`,{headers});if(!res.ok){showNotice('Аналитика недоступна.',true);return;}const data=await res.json();state.worker_activity=data.activity||[];state.worker_shifts=data.shifts||[];state.shift_plans=data.shift_plans||[];state.attendance_deviations=data.attendance_deviations||[];renderWorkerActivity();renderShiftPlans();}
async function markInboxRead(id){const res=await fetch('/api/inbox/read',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),item_id:Number(id)})});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice('Сообщение не обновлено.',true);return;}state.inbox_items=data.inbox_items||[];renderInbox();}
async function saveShiftPlan(){const worker=val('shiftPlanWorker'),start=val('shiftPlanStart'),end=val('shiftPlanEnd'),area=val('shiftPlanArea');if(!worker||!start||!end){showNotice('Укажите сотрудника, начало и окончание.',true);return;}const body={chat_id:Number(chatId),user_id:Number(userId),worker_user_id:Number(worker),area_id:area?Number(area):null,planned_start:start,planned_end:end,note:val('shiftPlanNote')};const res=await fetch('/api/shift-plans',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Плановая смена не создана.',true);return;}state.shift_plans=data.shift_plans||[];state.attendance_deviations=data.attendance_deviations||[];byId('shiftPlanNote').value='';renderShiftPlans();showNotice(data.message||'Плановая смена создана.');}
async function cancelShiftPlan(id){const res=await fetch(`/api/shift-plans?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&plan_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'План не отменён.',true);return;}state.shift_plans=data.shift_plans||[];state.attendance_deviations=data.attendance_deviations||[];renderShiftPlans();showNotice(data.message||'План отменён.');}
async function saveNotificationPreferences(){const body={chat_id:Number(chatId),user_id:Number(userId),inbox_enabled:byId('notifyInbox').checked,telegram_enabled:byId('notifyTelegram').checked,inventory_approval_enabled:byId('notifyInventoryApproval').checked,inventory_result_enabled:byId('notifyInventoryResult').checked,shift_plan_enabled:byId('notifyShiftPlan').checked,approval_reminders_enabled:byId('notifyApprovalReminders').checked,reminder_after_minutes:Number(val('notifyReminderAfter')||60),repeat_every_minutes:Number(val('notifyRepeatEvery')||120),max_reminders:Number(val('notifyMaxReminders')||3)};const res=await fetch('/api/notification-preferences',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Настройки не сохранены.',true);return;}state.notification_preferences=data.notification_preferences||{};renderNotificationPreferences();showNotice(data.message||'Настройки сохранены.');}
async function saveShiftTemplate(){const worker=val('shiftTemplateWorker'),validFrom=val('shiftTemplateValidFrom'),pattern=val('shiftTemplatePattern');if(!worker||!validFrom){showNotice('Укажите сотрудника и дату начала графика.',true);return;}const weekdays=[...document.querySelectorAll('[data-shift-weekday]:checked')].map(x=>Number(x.dataset.shiftWeekday));const body={chat_id:Number(chatId),user_id:Number(userId),template_id:val('shiftTemplateId')?Number(val('shiftTemplateId')):null,worker_user_id:Number(worker),area_id:val('shiftTemplateArea')?Number(val('shiftTemplateArea')):null,pattern_type:pattern,weekdays,cycle_work_days:Number(val('shiftTemplateWorkDays')||2),cycle_rest_days:Number(val('shiftTemplateRestDays')||2),cycle_anchor_date:val('shiftTemplateAnchor')||validFrom,start_time:val('shiftTemplateStartTime')||'09:00',end_time:val('shiftTemplateEndTime')||'18:00',valid_from:validFrom,valid_until:val('shiftTemplateValidUntil')||null,enabled:true,note:val('shiftTemplateNote')};const res=await fetch('/api/shift-templates',{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'График не сохранён.',true);return;}state.shift_templates=data.shift_templates||[];state.shift_plans=data.shift_plans||[];state.shift_calendar=data.shift_calendar||[];renderShiftTemplates();renderShiftPlans();renderShiftCalendar();clearShiftTemplate();showNotice(data.message||'График сохранён.');}
async function disableShiftTemplate(id){const res=await fetch(`/api/shift-templates?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}&template_id=${encodeURIComponent(id)}`,{method:'DELETE',headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'График не отключён.',true);return;}state.shift_templates=data.shift_templates||[];state.shift_plans=data.shift_plans||[];renderShiftTemplates();renderShiftPlans();showNotice(data.message||'График отключён.');}
async function loadShiftCalendar(){const from=val('shiftCalendarFrom'),to=val('shiftCalendarTo');if(!from||!to){showNotice('Укажите период календаря.',true);return;}const q=new URLSearchParams({chat_id:chatId,user_id:userId,start_date:from,end_date:to});if(val('shiftCalendarWorker'))q.set('worker_user_id',val('shiftCalendarWorker'));if(val('shiftCalendarArea'))q.set('area_id',val('shiftCalendarArea'));const res=await fetch('/api/shift-calendar?'+q.toString(),{headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Календарь недоступен.',true);return;}state.shift_calendar=data.shift_calendar||[];renderShiftCalendar();}
async function loadAttendance(){const from=val('attendanceFrom'),to=val('attendanceTo');if(!from||!to){showNotice('Укажите период посещаемости.',true);return;}const q=new URLSearchParams({chat_id:chatId,user_id:userId,start_date:from,end_date:to});if(val('attendanceWorker'))q.set('worker_user_id',val('attendanceWorker'));if(val('attendanceArea'))q.set('area_id',val('attendanceArea'));const res=await fetch('/api/attendance-summary?'+q.toString(),{headers});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Посещаемость недоступна.',true);return;}state.attendance_summary=data.attendance_summary||[];state.attendance_details=data.attendance_details||[];renderAttendanceSummary();}
async function downloadAttendance(format){const from=val('attendanceFrom'),to=val('attendanceTo');if(!from||!to){showNotice('Укажите период посещаемости.',true);return;}const q=new URLSearchParams({chat_id:chatId,user_id:userId,start_date:from,end_date:to,report_format:format});if(val('attendanceWorker'))q.set('worker_user_id',val('attendanceWorker'));if(val('attendanceArea'))q.set('area_id',val('attendanceArea'));const res=await fetch('/api/attendance-export?'+q.toString(),{headers});if(!res.ok){const data=await res.json().catch(()=>({}));showNotice(data.detail||'Файл не создан.',true);return;}await downloadBlob(res,`attendance.${format}`);}
async function restoreAccount(){const file=byId('restoreBackupFile')?.files?.[0];const confirmation=val('restoreConfirmation');if(!file){showNotice('Выберите файл копии.',true);return;}if(confirmation.trim().toUpperCase()!=='ВОССТАНОВИТЬ'){showNotice('Введите слово ВОССТАНОВИТЬ.',true);return;}if(file.size>25*1024*1024){showNotice('Файл копии слишком большой.',true);return;}const bytes=new Uint8Array(await file.arrayBuffer());let binary='';const chunk=0x8000;for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,i+chunk));const content_base64=btoa(binary);const res=await fetch('/api/restore',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),filename:file.name,content_base64,confirmation})});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Восстановление не выполнено.',true);return;}showNotice(data.message||'Учёт восстановлен.');byId('restoreConfirmation').value='';byId('restoreBackupFile').value='';await load();}
async function retryReport(scheduleId,historyId=null){if(!scheduleId){showNotice('Расписание уже удалено.',true);return;}const res=await fetch('/api/report-deliveries/retry',{method:'POST',headers,body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),schedule_id:Number(scheduleId),history_id:historyId?Number(historyId):null})});const data=await res.json().catch(()=>({}));if(!res.ok){showNotice(data.detail||'Повтор не поставлен в очередь.',true);return;}state.report_delivery_history=data.delivery_history||[];renderReportDeliveryHistory();showNotice(data.message||'Отправка поставлена в очередь.');}
async function downloadBackup(){
  const res=await fetch(`/api/backup?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(userId)}`,{headers});
  if(!res.ok){showNotice('Копия недоступна.', true);return;} await downloadBlob(res, 'backup.zip');
}
async function downloadBlob(res, name){const blob=await res.blob(); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=name; a.click(); URL.revokeObjectURL(url);}
document.addEventListener('click', e=>{
  const account=e.target.closest('[data-chat]')?.dataset.chat; if(account){ chatId=account; localStorage.setItem('prodMiniChatId', account); location.search = `?chat_id=${encodeURIComponent(account)}${userId?`&user_id=${encodeURIComponent(userId)}`:''}${token?`&token=${encodeURIComponent(token)}`:''}`; return; }
  const editDestination=e.target.closest('[data-edit-destination]')?.dataset.editDestination;if(editDestination){const item=(state.destinations||[]).find(x=>String(x.id)===String(editDestination));if(item){byId('destinationId').value=item.id;byId('destinationName').value=item.name;byId('destinationType').value=item.destination_type;showTab('places');}return;}
  const deleteDestinationId=e.target.closest('[data-delete-destination]')?.dataset.deleteDestination;if(deleteDestinationId){deleteDestination(deleteDestinationId);return;}
  const editRule=e.target.closest('[data-edit-area-rule]')?.dataset.editAreaRule;if(editRule){const [job,area,section]=editRule.split(':');const item=(state.area_access_rules||[]).find(x=>String(x.job_title_id)===job&&String(x.area_id)===area&&x.section_key===section);if(item){byId('accessJob').value=job;byId('accessArea').value=area;byId('accessSection').value=section;byId('accessView').checked=!!item.can_view;byId('accessSubmit').checked=!!item.can_submit;byId('accessEdit').checked=!!item.can_edit;showTab('area-access');}return;}
  const deleteRule=e.target.closest('[data-delete-area-rule]')?.dataset.deleteAreaRule;if(deleteRule){deleteAreaRule(deleteRule);return;}
  const editJob=e.target.closest('[data-edit-job-title]')?.dataset.editJobTitle;if(editJob){const item=(state.job_titles||[]).find(x=>String(x.id)===String(editJob));if(item){clearJobTitle();byId('jobTitleId').value=item.id;byId('jobTitleName').value=item.name;document.querySelectorAll('[data-job-permission]').forEach(x=>x.checked=!!item.permissions?.[x.dataset.jobPermission]);showTab('team');}return;}
  const deleteJob=e.target.closest('[data-delete-job-title]')?.dataset.deleteJobTitle;if(deleteJob){deleteJobTitle(deleteJob);return;}
  const editWorker=e.target.closest('[data-edit-worker]')?.dataset.editWorker;if(editWorker){const item=(state.workers||[]).find(x=>String(x.user_id)===String(editWorker));if(item){byId('workerUserId').value=item.user_id;byId('workerUserId').readOnly=true;byId('workerName').value=item.display_name||'';byId('workerJob').value=item.job_title_id||'';showTab('team');}return;}
  const deleteWorkerId=e.target.closest('[data-delete-worker]')?.dataset.deleteWorker;if(deleteWorkerId){deleteWorker(deleteWorkerId);return;}
  const editDepartment=e.target.closest('[data-edit-department]')?.dataset.editDepartment;if(editDepartment){const item=(state.departments||[]).find(x=>String(x.id)===String(editDepartment));if(item){byId('departmentId').value=item.id;byId('departmentName').value=item.name;byId('departmentDescription').value=item.description||'';showTab('departments');}return;}
  const deleteDepartmentId=e.target.closest('[data-delete-department]')?.dataset.deleteDepartment;if(deleteDepartmentId){deleteDepartment(deleteDepartmentId);return;}
  const deleteDepartmentOperationId=e.target.closest('[data-delete-department-operation]')?.dataset.deleteDepartmentOperation;if(deleteDepartmentOperationId){deleteDepartmentOperation(deleteDepartmentOperationId);return;}
  const deleteDepartmentEntityId=e.target.closest('[data-delete-department-entity]')?.dataset.deleteDepartmentEntity;if(deleteDepartmentEntityId){deleteDepartmentEntity(deleteDepartmentEntityId);return;}
  const editDepartmentMember=e.target.closest('[data-edit-department-member]')?.dataset.editDepartmentMember;if(editDepartmentMember){const [depId,memberId]=editDepartmentMember.split(':');const dep=(state.departments||[]).find(x=>String(x.id)===depId);const member=(dep?.members||[]).find(x=>String(x.user_id)===memberId);if(member){byId('departmentMemberDepartment').value=depId;renderDepartmentMemberOperationChecks();byId('departmentMemberUserId').value=member.user_id;byId('departmentMemberName').value=member.display_name||'';byId('departmentMemberRole').value=member.role_level||20;(member.operation_keys||[]).forEach(op=>{const el=document.querySelector(`[data-department-member-operation="${op}"]`);if(el)el.checked=true;});showTab('departments');}return;}
  const deleteDepartmentMemberId=e.target.closest('[data-delete-department-member]')?.dataset.deleteDepartmentMember;if(deleteDepartmentMemberId){deleteDepartmentMember(deleteDepartmentMemberId);return;}
  const ackRisk=e.target.closest('[data-ack-risk]')?.dataset.ackRisk;if(ackRisk){acknowledgeRisk(ackRisk);return;}
  const resolveEvent=e.target.closest('[data-resolve-risk-event]')?.dataset.resolveRiskEvent;if(resolveEvent){resolveRiskEvent(resolveEvent);return;}
  const editStockRule=e.target.closest('[data-edit-stock-rule]')?.dataset.editStockRule;if(editStockRule){applyStockRule((state.stock_risk?.rules||[]).find(x=>String(x.id)===String(editStockRule)));return;}
  const deleteStockRuleId=e.target.closest('[data-delete-stock-rule]')?.dataset.deleteStockRule;if(deleteStockRuleId){deleteStockRule(deleteStockRuleId);return;}
  const inventoryKey=e.target.closest('[data-select-inventory]')?.dataset.selectInventory;if(inventoryKey){const [area,type,entityId]=inventoryKey.split(':');byId('inventoryArea').value=area;byId('inventoryType').value=type;updateInventoryEntities();byId('inventoryEntity').value=entityId;updateInventoryCurrent();showTab('inventory');loadInventoryHistory();return;}
  const usePreset=e.target.closest('[data-use-report-preset]')?.dataset.useReportPreset;if(usePreset){applyReportPreset((state.report_presets||[]).find(x=>String(x.id)===String(usePreset)),false);return;}
  const editPreset=e.target.closest('[data-edit-report-preset]')?.dataset.editReportPreset;if(editPreset){applyReportPreset((state.report_presets||[]).find(x=>String(x.id)===String(editPreset)),true);return;}
  const deletePreset=e.target.closest('[data-delete-report-preset]')?.dataset.deleteReportPreset;if(deletePreset){deleteReportPreset(deletePreset);return;}
  const openSession=e.target.closest('[data-open-inventory-session]')?.dataset.openInventorySession;if(openSession){openInventorySession(openSession);return;}
  const deleteSessionItem=e.target.closest('[data-delete-inventory-session-item]')?.dataset.deleteInventorySessionItem;if(deleteSessionItem){deleteInventorySessionItem(deleteSessionItem);return;}
  const editSchedule=e.target.closest('[data-edit-report-schedule]')?.dataset.editReportSchedule;if(editSchedule){applyReportSchedule((state.report_schedules||[]).find(x=>String(x.id)===String(editSchedule)));return;}
  const deleteSchedule=e.target.closest('[data-delete-report-schedule]')?.dataset.deleteReportSchedule;if(deleteSchedule){deleteReportSchedule(deleteSchedule);return;}
  const retrySchedule=e.target.closest('[data-retry-report-schedule]')?.dataset.retryReportSchedule;if(retrySchedule){retryReport(retrySchedule);return;}
  const retryHistory=e.target.closest('[data-retry-report-history]');if(retryHistory){retryReport(retryHistory.dataset.scheduleId,retryHistory.dataset.retryReportHistory);return;}
  const readInbox=e.target.closest('[data-read-inbox]')?.dataset.readInbox;if(readInbox){markInboxRead(readInbox);return;}
  const openInbox=e.target.closest('[data-open-inbox-session]');if(openInbox){markInboxRead(openInbox.dataset.inboxId);openInventorySession(openInbox.dataset.openInboxSession);return;}
    const cancelPlan=e.target.closest('[data-cancel-shift-plan]')?.dataset.cancelShiftPlan;if(cancelPlan){cancelShiftPlan(cancelPlan);return;}
  const editTemplate=e.target.closest('[data-edit-shift-template]')?.dataset.editShiftTemplate;if(editTemplate){applyShiftTemplate((state.shift_templates||[]).find(x=>String(x.id)===String(editTemplate)));return;}
  const disableTemplate=e.target.closest('[data-disable-shift-template]')?.dataset.disableShiftTemplate;if(disableTemplate){disableShiftTemplate(disableTemplate);return;}
  const period=e.target.closest('[data-period]')?.dataset.period; if(period){byId('reportText').value=period;return;}
  const tab=e.target.closest('[data-tab]')?.dataset.tab; if(tab){showTab(tab);return;}
  const action=e.target.closest('[data-action]')?.dataset.action; if(!action)return;
  if(action==='save-work-entry') { saveWorkEntry(); return; }
  if(action==='refresh-stock-risks') { refreshStockRisk(); return; }
  if(action==='save-stock-observation') { saveStockObservation(); return; }
  if(action==='save-risk-event') { saveRiskEvent(); return; }
  if(action==='save-stock-rule') { saveStockRule(); return; }
  if(action==='clear-stock-rule') { clearStockRule(); return; }
  if(action==='save-production') postOperation('production', entityTypeById(val('productionEntity')), val('productionEntity'), qty('productionQty'), val('productionArea'), {note:val('productionNote')});
  if(action==='save-material-in') postOperation('material_in','material',val('materialEntity'),qty('materialQty'),val('materialArea'), {note:val('materialNote')});
  if(action==='save-material-out') postOperation('material_out','material',val('materialEntity'),qty('materialQty'),val('materialArea'), {note:val('materialNote')});
  if(action==='save-assembly') postOperation('assembly','product',val('assemblyEntity'),qty('assemblyQty'),val('assemblyArea'), {note:val('assemblyNote'),destination_type:'storage',storage_place:destinationName(val('assemblyStorage'))});
  if(action==='save-movement') postOperation('movement',val('moveType'),val('moveEntity'),qty('moveQty'),null,{from_area_id:val('moveFrom')?Number(val('moveFrom')):null,to_area_id:val('moveTo')?Number(val('moveTo')):null,note:val('moveNote')});
  if(action==='save-shipment') postOperation(val('shipKind'),'product',val('shipEntity'),qty('shipQty'),val('shipArea'), {note:val('shipNote'), destination_type:val('shipKind')==='shipment_fulfillment'?'fulfillment':'client',storage_place:destinationName(val('shipDestination'))});
  if(action==='save-return') postOperation('return','product',val('returnEntity'),qty('returnQty'),val('returnArea'), {note:val('returnNote'),destination_type:'storage',storage_place:destinationName(val('returnStorage'))});
  if(action==='save-plan') savePlan();
  if(action==='clear-plan') clearPlan();
  if(action==='report-xlsx') downloadReport('xlsx');
  if(action==='report-pdf') downloadReport('pdf');
  if(action==='save-report-preset') saveReportPreset();
  if(action==='clear-report-preset') clearReportPreset();
  if(action==='save-destination') saveDestination();
  if(action==='clear-destination') clearDestination();
  if(action==='save-area-access') saveAreaAccess();
  if(action==='save-department') saveDepartment();
  if(action==='clear-department') clearDepartment();
  if(action==='save-department-operation') saveDepartmentOperation();
  if(action==='save-department-entity') saveDepartmentEntity();
  if(action==='save-department-member') saveDepartmentMember();
  if(action==='save-job-title') saveJobTitle();
  if(action==='clear-job-title') clearJobTitle();
  if(action==='save-worker') saveWorker();
  if(action==='clear-worker') clearWorker();
  if(action==='save-inventory-correction') saveInventoryCorrection();
  if(action==='refresh-inventory-history') loadInventoryHistory();
  if(action==='create-inventory-session') createInventorySession();
  if(action==='clear-inventory-session') clearInventorySession();
  if(action==='add-inventory-session-item') addInventorySessionItem();
  if(action==='submit-inventory-session') inventorySessionAction('submit');
  if(action==='approve-inventory-session') inventorySessionAction('approve');
  if(action==='reject-inventory-session') inventorySessionAction('reject');
  if(action==='cancel-inventory-session') inventorySessionAction('cancel');
  if(action==='save-report-schedule') saveReportSchedule();
  if(action==='save-shift-plan') saveShiftPlan();
  if(action==='save-shift-template') saveShiftTemplate();
  if(action==='clear-shift-template') clearShiftTemplate();
  if(action==='load-shift-calendar') loadShiftCalendar();
  if(action==='load-attendance') loadAttendance();
  if(action==='attendance-xlsx') downloadAttendance('xlsx');
  if(action==='attendance-pdf') downloadAttendance('pdf');
  if(action==='save-notification-preferences') saveNotificationPreferences();
  if(action==='restore-account') restoreAccount();
  if(action==='start-shift') shiftAction('start');
  if(action==='end-shift') shiftAction('end');
  if(action==='refresh-worker-activity') loadWorkerActivity();
  if(action==='backup-account') downloadBackup();
});
byId('workOperation')?.addEventListener('change', updateWorkEntry);
byId('moveType')?.addEventListener('change', updateMoveEntities);
byId('shipKind')?.addEventListener('change', updateShipDestinations);
byId('inventorySessionType')?.addEventListener('change', updateInventorySessionEntities);
byId('departmentEntityType')?.addEventListener('change', updateDepartmentEntityChoices);
byId('departmentMemberDepartment')?.addEventListener('change', renderDepartmentMemberOperationChecks);
byId('scheduleFrequency')?.addEventListener('change', updateScheduleFields);
byId('shiftTemplatePattern')?.addEventListener('change', updateShiftTemplateFields);
byId('inventoryType')?.addEventListener('change', ()=>{updateInventoryEntities();loadInventoryHistory();});
byId('inventoryEntity')?.addEventListener('change', ()=>{updateInventoryCurrent();loadInventoryHistory();});
byId('inventoryArea')?.addEventListener('change', ()=>{updateInventoryCurrent();loadInventoryHistory();});
byId('riskObservationType')?.addEventListener('change',updateRiskObservationEntities);
byId('riskObservationMode')?.addEventListener('change',updateRiskObservationEntities);
byId('stockRuleType')?.addEventListener('change',updateRiskRuleEntities);
byId('riskEventType')?.addEventListener('change',updateRiskEventEntities);
load();
