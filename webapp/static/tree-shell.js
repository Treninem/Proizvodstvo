// Mini App hierarchical tree shell: 20260820b
(() => {
  'use strict';

  document.documentElement.classList.add('tree-mode');

  const tree = {
    snapshot: null,
    access: {is_primary_owner:false,is_tenant_admin:false,can_manage:false,can_manage_departments:false,permissions:{}},
    currentMenu: 'root',
    history: [],
    mounted: null,
    booted: false,
    loading: false,
  };

  const entityLabels = {
    material: 'Сырьё',
    component: 'Комплектующие',
    product: 'Изделия',
    stock_item: 'Складские позиции',
    meter: 'Счётчики',
  };

  const menus = {
    root: {
      title: 'Главное меню',
      subtitle: 'Выберите, что нужно сделать.',
      items: [
        {kind:'menu', key:'work', title:'Работа', hint:'Ввод производства, сырья, сборки и перемещений'},
        {kind:'menu', key:'warehouse', title:'Склад', hint:'Остатки, передачи, инвентаризация и критические запасы'},
        {kind:'menu', key:'planning', title:'Планирование и контроль', hint:'Задания, смены, качество, оборудование и входящие'},
        {kind:'menu', key:'reports', title:'Отчёты', hint:'Отчёты, план/факт и выгрузки', permission:'reports'},
        {kind:'menu', key:'organization', title:'Настроить организацию', hint:'Структура, справочники, сотрудники и права', manage:true},
        {kind:'menu', key:'owner', title:'Владелец', hint:'Закрытое управление системой', owner:true},
        {kind:'leaf', key:'help', title:'Как пользоваться', hint:'Пошаговая инструкция', leaf:{type:'page', tab:'help'}},
      ],
    },
    work: {
      title: 'Работа', subtitle:'Каждое действие открывается отдельным экраном.', items:[
        {kind:'leaf',key:'work-entry',title:'Рабочий ввод',hint:'Основной ввод по доступным действиям',leaf:{type:'panel',tab:'work',heading:'Рабочий ввод'},work:true},
        {kind:'leaf',key:'production',title:'Добавить выпуск',hint:'Записать изготовленную продукцию',permission:'production',leaf:{type:'page',tab:'production'}},
        {kind:'leaf',key:'material-operation',title:'Приход / расход сырья',hint:'Рабочая операция с материалами',permission:'material',leaf:{type:'page',tab:'materials'}},
        {kind:'leaf',key:'assembly',title:'Сборка изделия',hint:'Записать собранную продукцию',permission:'assembly',leaf:{type:'page',tab:'assembly'}},
        {kind:'leaf',key:'movement',title:'Перемещение',hint:'Передать позицию между участками',permission:'movement',leaf:{type:'page',tab:'movement'}},
        {kind:'leaf',key:'shipment',title:'Отгрузка',hint:'Отправить продукцию',permission:'shipment',leaf:{type:'page',tab:'shipment'}},
        {kind:'leaf',key:'returns',title:'Возврат',hint:'Вернуть продукцию на хранение',permission:'returns',leaf:{type:'page',tab:'returns'}},
      ],
    },
    warehouse: {
      title:'Склад', subtitle:'Остатки и движения разнесены по отдельным экранам.', items:[
        {kind:'leaf',key:'stock-overview',title:'Остатки и местонахождение',hint:'Что есть и где находится',leaf:{type:'page',tab:'overview'}},
        {kind:'leaf',key:'transfers',title:'Передачи',hint:'Отправить или принять между отделами',leaf:{type:'page',tab:'transfers'}},
        {kind:'leaf',key:'inventory',title:'Инвентаризация',hint:'Фактический пересчёт',permission:'stock',leaf:{type:'page',tab:'inventory'}},
        {kind:'leaf',key:'risks',title:'Критические остатки',hint:'Запасы, пороги и предупреждения',leaf:{type:'page',tab:'risks'}},
        {kind:'leaf',key:'destinations',title:'Места и направления',hint:'Склады, клиенты и точки отправки',manage:true,leaf:{type:'page',tab:'places'}},
      ],
    },
    planning: {
      title:'Планирование и контроль', subtitle:'Выберите направление, затем конкретное действие.', items:[
        {kind:'menu',key:'tasks',title:'Задания и заявки',hint:'Производственные задания, заявки и партии'},
        {kind:'menu',key:'shifts',title:'Смены',hint:'Текущие, плановые и передача смены'},
        {kind:'menu',key:'quality',title:'Качество и снабжение',hint:'Контроль, пополнение и обслуживание'},
        {kind:'leaf',key:'plan',title:'План сборки',hint:'Цели по изделиям',permission:'assembly',leaf:{type:'page',tab:'plan'}},
        {kind:'leaf',key:'inbox',title:'Входящие',hint:'То, что требует внимания',leaf:{type:'page',tab:'inbox'}},
        {kind:'leaf',key:'control',title:'Контроль смены',hint:'Сводка руководителя',control:true,leaf:{type:'page',tab:'control'}},
      ],
    },
    tasks: {
      title:'Задания и заявки', subtitle:'Один экран — одна задача.', items:[
        {kind:'leaf',key:'task-list',title:'Список заданий',hint:'Активные и завершённые задания',leaf:{type:'panel',tab:'workflow',heading:'Задания'}},
        {kind:'leaf',key:'task-create',title:'Создать задание',hint:'Новое производственное задание',control:true,leaf:{type:'panel',tab:'workflow',heading:'Новое задание'}},
        {kind:'leaf',key:'request-list',title:'Заявки между отделами',hint:'Статусы и движение заявок',leaf:{type:'panel',tab:'workflow',heading:'Заявки между отделами'}},
        {kind:'leaf',key:'request-create',title:'Создать заявку',hint:'Запросить позицию у другого отдела',leaf:{type:'panel',tab:'workflow',heading:'Новая заявка'}},
        {kind:'leaf',key:'lot-list',title:'Партии и прослеживаемость',hint:'Список партий и связи',leaf:{type:'panel',tab:'workflow',heading:'Партии и прослеживаемость'}},
        {kind:'leaf',key:'lot-create',title:'Создать партию',hint:'Новая производственная партия',control:true,leaf:{type:'panel',tab:'workflow',heading:'Создать партию'}},
        {kind:'leaf',key:'equipment-list',title:'Оборудование',hint:'Список оборудования и состояние',leaf:{type:'panel',tab:'workflow',heading:'Оборудование, простои'}},
        {kind:'leaf',key:'equipment-create',title:'Добавить оборудование',hint:'Название, код и период обслуживания',control:true,leaf:{type:'panel',tab:'workflow',heading:'Оборудование'}},
        {kind:'leaf',key:'plan-fact',title:'План / факт',hint:'Сводка выполнения',leaf:{type:'panel',tab:'workflow',heading:'План / факт'}},
      ],
    },
    shifts: {
      title:'Смены', subtitle:'Каждый этап работы со сменой открыт отдельно.', items:[
        {kind:'leaf',key:'shift-current',title:'Рабочая смена',hint:'Начать или завершить смену',leaf:{type:'panel',tab:'shifts',heading:'Рабочая смена'}},
        {kind:'leaf',key:'shift-activity',title:'Активность сотрудников',hint:'Факт работы за период',leaf:{type:'panel',tab:'shifts',heading:'Активность сотрудников'}},
        {kind:'leaf',key:'handover',title:'Передать смену',hint:'Передать незавершённое следующему сотруднику',leaf:{type:'panel',tab:'shifts',heading:'Передача смены'}},
        {kind:'leaf',key:'handover-log',title:'Журнал передачи смен',hint:'История передач',leaf:{type:'panel',tab:'shifts',heading:'Журнал передачи смен'}},
        {kind:'leaf',key:'shift-plan-create',title:'Назначить плановую смену',hint:'Сотрудник, время и площадка',manage:true,leaf:{type:'panel',tab:'shifts',heading:'Плановая смена'}},
        {kind:'leaf',key:'shift-plan-list',title:'План смен',hint:'Назначенные смены',leaf:{type:'panel',tab:'shifts',heading:'План смен'}},
        {kind:'leaf',key:'shift-template',title:'Повторяющийся график',hint:'Недельный или циклический график',manage:true,leaf:{type:'panel',tab:'shifts',heading:'Повторяющийся график'}},
      ],
    },
    quality: {
      title:'Качество и снабжение', subtitle:'Контроль и обслуживание разделены.', items:[
        {kind:'leaf',key:'quality-list',title:'Контроль качества',hint:'Проверки и результаты',leaf:{type:'panel',tab:'quality',heading:'Контроль качества'}},
        {kind:'leaf',key:'quality-create',title:'Записать проверку',hint:'Новая проверка качества',leaf:{type:'panel',tab:'quality',heading:'Записать проверку'}},
        {kind:'leaf',key:'quality-rule',title:'Правило контроля',hint:'Обязательность, выборка и карантин',control:true,leaf:{type:'panel',tab:'quality',heading:'Правило контроля'}},
        {kind:'leaf',key:'replenishment-list',title:'Дефицит и заявки',hint:'Что нужно пополнить',leaf:{type:'panel',tab:'quality',heading:'Прогноз дефицита'}},
        {kind:'leaf',key:'replenishment-setting',title:'Настройка пополнения',hint:'Поставка и целевой запас',control:true,leaf:{type:'panel',tab:'quality',heading:'Настройка пополнения'}},
        {kind:'leaf',key:'maintenance-calendar',title:'Календарь ТО',hint:'Сроки обслуживания',leaf:{type:'panel',tab:'quality',heading:'Календарь ТО'}},
        {kind:'leaf',key:'maintenance-plan',title:'План обслуживания',hint:'Интервал, ответственный и чек-лист',control:true,leaf:{type:'panel',tab:'quality',heading:'План обслуживания'}},
        {kind:'leaf',key:'needs-report',title:'Потребность и дефицит',hint:'План → наличие → дефицит → факт',leaf:{type:'panel',tab:'quality',heading:'План → потребность'}},
      ],
    },
    reports: {
      title:'Отчёты', subtitle:'Выберите нужный вид отчёта.', items:[
        {kind:'leaf',key:'reports-main',title:'Отчёты за период',hint:'Формирование и выгрузка',permission:'reports',leaf:{type:'page',tab:'reports'}},
        {kind:'leaf',key:'reports-plan-fact',title:'План / факт заданий',hint:'Выполнение производственного плана',leaf:{type:'panel',tab:'workflow',heading:'План / факт'}},
        {kind:'leaf',key:'reports-needs',title:'Потребность производства',hint:'План, запас и дефицит',leaf:{type:'panel',tab:'quality',heading:'План → потребность'}},
      ],
    },
    organization: {
      title:'Настроить организацию', subtitle:'Настройки разложены по отдельным шагам.', items:[
        {kind:'leaf',key:'units',title:'Единицы измерения',hint:'Создать список: кг, мешок, шт, ед. и другие',manage:true,leaf:{type:'custom',screen:'units'}},
        {kind:'leaf',key:'add-material',title:'Добавить сырьё',hint:'Несколько позиций за одно сохранение',manage:true,leaf:{type:'custom',screen:'entity-batch',entityType:'material'}},
        {kind:'leaf',key:'add-components',title:'Добавить комплектующие',hint:'Несколько комплектующих за одно сохранение',manage:true,leaf:{type:'custom',screen:'entity-batch',entityType:'component'}},
        {kind:'leaf',key:'add-products',title:'Добавить изделия',hint:'Изделия и единицы учёта',manage:true,leaf:{type:'custom',screen:'entity-batch',entityType:'product'}},
        {kind:'leaf',key:'composition',title:'Состав изделия',hint:'Выбрать несколько или все комплектующие',manage:true,leaf:{type:'custom',screen:'composition'}},
        {kind:'leaf',key:'add-stock',title:'Добавить складские позиции',hint:'Позиции склада и места использования',manage:true,leaf:{type:'custom',screen:'entity-batch',entityType:'stock_item'}},
        {kind:'leaf',key:'add-meters',title:'Добавить счётчики',hint:'Счётчики и участки',manage:true,leaf:{type:'custom',screen:'entity-batch',entityType:'meter'}},
        {kind:'menu',key:'structure',title:'Структура и хранение',hint:'Площадки, участки и конкретные места'},
        {kind:'menu',key:'people',title:'Сотрудники и права',hint:'Должности, назначения, отделы и доступ'},
        {kind:'leaf',key:'organization-destinations',title:'Места и направления',hint:'Склады, клиенты и точки отправки',manage:true,leaf:{type:'page',tab:'places'}},
      ],
    },
    structure: {
      title:'Структура и хранение', subtitle:'Настраивайте структуру по шагам.', items:[
        {kind:'leaf',key:'site-create',title:'Добавить площадку',hint:'Населённый пункт, название и адрес',manage:true,leaf:{type:'panel',tab:'organization',heading:'Населённый пункт / площадка'}},
        {kind:'leaf',key:'area-bind',title:'Привязать участок к площадке',hint:'Связать уже созданный участок',manage:true,leaf:{type:'panel',tab:'organization',heading:'Привязать участок'}},
        {kind:'leaf',key:'storage-location',title:'Добавить место хранения',hint:'Стеллаж, зона, склад или другое место',manage:true,leaf:{type:'panel',tab:'organization',heading:'Место хранения'}},
        {kind:'leaf',key:'area-create',title:'Добавить участок',hint:'Цех, линия, фасовка и другие зоны',manage:true,leaf:{type:'custom',screen:'area-batch'}},
      ],
    },
    people: {
      title:'Сотрудники и права', subtitle:'Сначала должности, затем назначение и доступы.', items:[
        {kind:'leaf',key:'jobs',title:'Должности',hint:'Создать должность и выбрать права',manage:true,leaf:{type:'panel',tab:'team',heading:'Должности'}},
        {kind:'leaf',key:'jobs-list',title:'Список должностей',hint:'Изменить существующую должность',manage:true,leaf:{type:'panel',tab:'team',heading:'Список должностей'}},
        {kind:'leaf',key:'assign-job',title:'Назначить должность',hint:'Telegram ID или @username + созданная должность',manage:true,leaf:{type:'custom',screen:'assign-worker'}},
        {kind:'leaf',key:'workers-list',title:'Список сотрудников',hint:'Кто добавлен в учёт',manage:true,leaf:{type:'panel',tab:'team',heading:'Список сотрудников'}},
        {kind:'leaf',key:'departments',title:'Отделы',hint:'Руководители и рабочие действия',manage:true,leaf:{type:'page',tab:'departments'}},
        {kind:'leaf',key:'area-access',title:'Права по участкам',hint:'Что разрешено каждой должности',manage:true,leaf:{type:'page',tab:'area-access'}},
      ],
    },
    owner: {
      title:'Владелец', subtitle:'Этот раздел формируется только для владельца системы.', items:[
        {kind:'leaf',key:'owner-security',title:'Администрирование',hint:'Резерв, устройства и журнал безопасности',owner:true,leaf:{type:'page',tab:'security'}},
        {kind:'leaf',key:'owner-diagnostics',title:'Диагностика',hint:'Проверка базы, бота, Mini App и очередей',owner:true,leaf:{type:'panel',tab:'control',heading:'Диагностика'}},
        {kind:'leaf',key:'owner-sla',title:'Время реакции',hint:'Настройка сроков критических событий',owner:true,leaf:{type:'panel',tab:'control',heading:'Время реакции'}},
      ],
    },
  };

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value ?? '').replace(/[&<>\"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[ch]));
  }

  function notify(message, bad=false) {
    if (typeof showNotice === 'function') showNotice(message, bad);
    const local = document.getElementById('treeLocalNotice');
    if (local) {
      local.textContent = String(message || '');
      local.classList.toggle('bad', !!bad);
      local.classList.toggle('hidden', !message);
      if (message) setTimeout(() => local.classList.add('hidden'), 5000);
    }
  }

  async function postJson(path, body) {
    const request = typeof apiFetch === 'function' ? apiFetch : fetch;
    const response = await request(path, {method:'POST', headers, body:JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data?.detail;
      throw new Error(typeof detail === 'string' ? detail : (detail?.message || 'Действие не выполнено.'));
    }
    return data;
  }

  function contextBody() {
    return {chat_id:Number(chatId), user_id:Number(userId)};
  }

  function permissionAllowed(permission) {
    if (!permission) return true;
    const p = tree.access.permissions || {};
    if (tree.access.can_manage) return true;
    if (p[permission]) return true;
    try { if (typeof can === 'function' && can(permission)) return true; } catch (e) {}
    return false;
  }

  function itemAllowed(item) {
    if (item.owner && !tree.access.is_primary_owner) return false;
    if (item.manage && !tree.access.can_manage) return false;
    if (item.control && !(tree.access.can_manage || tree.access.can_manage_departments)) return false;
    if (item.work && !(typeof state !== 'undefined' && (state.work_access || []).length)) return false;
    if (item.permission && !permissionAllowed(item.permission)) return false;
    return true;
  }

  function mergeSnapshot(data) {
    tree.snapshot = data || {};
    tree.access = data?.access || tree.access;
    if (typeof state !== 'undefined') {
      if (data?.areas) state.areas = data.areas;
      if (data?.entities) state.entities = {...(state.entities || {}), ...data.entities};
      if (data?.job_titles) state.job_titles = data.job_titles;
      if (data?.workers) state.workers = data.workers;
    }
    try { if (typeof fillForms === 'function') fillForms(); } catch(e) {}
    try { if (typeof renderTeam === 'function') renderTeam(); } catch(e) {}
    renderContextSelector();
  }

  async function loadSnapshot(force=false) {
    if (tree.loading || !chatId || !userId) return false;
    if (tree.snapshot && !force) return true;
    tree.loading = true;
    try {
      const data = await postJson('/api/tree/snapshot', contextBody());
      mergeSnapshot(data);
      return true;
    } catch (error) {
      notify(error.message, true);
      return false;
    } finally {
      tree.loading = false;
    }
  }

  function shell() {
    return document.getElementById('treeApp');
  }

  function ensureShell() {
    if (shell()) return;
    const host = document.querySelector('main.app-shell') || document.body;
    const node = document.createElement('section');
    node.id = 'treeApp';
    node.className = 'tree-app';
    node.innerHTML = `
      <header class="tree-topbar">
        <div class="tree-brand"><small>Производственный учёт</small><strong>Производство</strong></div>
        <button type="button" class="tree-home" data-tree-home>Главное меню</button>
      </header>
      <section class="tree-context-card">
        <label>Доступная группа / учёт
          <select id="treeAccountSelect"><option value="">Загрузка…</option></select>
        </label>
        <div id="treeAccessLabel" class="tree-access-label">Проверяем доступ…</div>
      </section>
      <section class="tree-navigation-bar">
        <button type="button" class="tree-back hidden" data-tree-back>Назад</button>
        <div><small id="treeBreadcrumb">Главное меню</small><h1 id="treeTitle">Главное меню</h1></div>
      </section>
      <div id="treeLocalNotice" class="tree-local-notice hidden"></div>
      <section id="treeStage" class="tree-stage"><div class="tree-loading">Загружаем доступные разделы…</div></section>`;
    const hero = host.querySelector('.hero');
    if (hero) host.insertBefore(node, hero);
    else host.prepend(node);
  }

  function renderContextSelector() {
    const select = document.getElementById('treeAccountSelect');
    if (!select) return;
    const accounts = typeof state !== 'undefined' ? (state.accounts || []) : [];
    const current = String(chatId || '');
    select.innerHTML = accounts.length
      ? accounts.map(account => `<option value="${account.id}" data-scope="${account.scope_chat_id}" ${String(account.scope_chat_id)===current?'selected':''}>${esc(account.name)}</option>`).join('')
      : '<option value="">Нет доступных групп</option>';
    const access = document.getElementById('treeAccessLabel');
    if (access) {
      access.textContent = tree.access.is_primary_owner
        ? 'Владелец'
        : (tree.access.can_manage ? 'Управляющий учётом' : (tree.access.can_manage_departments ? 'Руководитель отдела' : 'Сотрудник'));
    }
  }

  async function switchAccount(accountId) {
    if (!accountId || !userId) return;
    try {
      const data = await postJson('/api/accounts/select', {user_id:Number(userId), account_id:Number(accountId)});
      const scopeId = data.active_scope_chat_id || data.scope_chat_id;
      if (scopeId) localStorage.setItem('prodMiniChatId', String(scopeId));
      location.reload();
    } catch (error) {
      notify(error.message, true);
      renderContextSelector();
    }
  }

  function restoreMounted() {
    const mounted = tree.mounted;
    if (!mounted) return;
    const {node,parent,next} = mounted;
    if (node && parent) {
      if (next && next.parentNode === parent) parent.insertBefore(node, next);
      else parent.appendChild(node);
    }
    tree.mounted = null;
  }

  function setHeader(title, breadcrumb) {
    const titleNode = document.getElementById('treeTitle');
    const crumbNode = document.getElementById('treeBreadcrumb');
    if (titleNode) titleNode.textContent = title || 'Раздел';
    if (crumbNode) crumbNode.textContent = breadcrumb || 'Главное меню';
    document.querySelector('[data-tree-back]')?.classList.toggle('hidden', tree.history.length === 0);
  }

  function menuBreadcrumb(menuKey) {
    if (menuKey === 'root') return 'Главное меню';
    const titles = tree.history.map(entry => menus[entry]?.title).filter(Boolean);
    titles.push(menus[menuKey]?.title || 'Раздел');
    return titles.join(' / ');
  }

  function renderMenu(menuKey, pushHistory=true) {
    restoreMounted();
    const menu = menus[menuKey];
    if (!menu) return;
    if (pushHistory && tree.currentMenu && tree.currentMenu !== menuKey) tree.history.push(tree.currentMenu);
    tree.currentMenu = menuKey;
    const stage = document.getElementById('treeStage');
    if (!stage) return;
    const visible = menu.items.filter(itemAllowed);
    stage.innerHTML = `
      <div class="tree-menu-intro"><p>${esc(menu.subtitle || '')}</p></div>
      <div class="tree-menu-list">${visible.map(item => `
        <button type="button" class="tree-menu-card" data-tree-item="${esc(item.key)}" data-tree-kind="${esc(item.kind)}">
          <span><b>${esc(item.title)}</b><small>${esc(item.hint || '')}</small></span><i>›</i>
        </button>`).join('')}</div>`;
    setHeader(menu.title, menuBreadcrumb(menuKey));
    window.scrollTo({top:0,behavior:'instant'});
  }

  function findItem(menuKey, itemKey) {
    return menus[menuKey]?.items.find(item => item.key === itemKey) || null;
  }

  function activateLegacy(tab) {
    try { if (typeof showTab === 'function') showTab(tab); } catch(e) {}
  }

  function mountNode(node, title, tab) {
    if (!node) {
      notify('Этот экран пока не найден в текущей сборке.', true);
      return;
    }
    restoreMounted();
    const parent = node.parentNode;
    const next = node.nextSibling;
    tree.mounted = {node,parent,next};
    const stage = document.getElementById('treeStage');
    stage.innerHTML = '';
    node.classList.add('tree-mounted-content');
    stage.appendChild(node);
    if (node.classList.contains('tab-page')) node.classList.add('active');
    setHeader(title, `${menus[tree.currentMenu]?.title || 'Меню'} / ${title}`);
    if (tab) activateLegacy(tab);
    window.scrollTo({top:0,behavior:'instant'});
  }

  function openLegacyPage(leaf, title) {
    activateLegacy(leaf.tab);
    const node = document.getElementById(`page-${leaf.tab}`);
    mountNode(node, title, leaf.tab);
  }

  function openLegacyPanel(leaf, title) {
    activateLegacy(leaf.tab);
    const page = document.getElementById(`page-${leaf.tab}`);
    if (!page) return mountNode(null, title, leaf.tab);
    const headingNeedle = String(leaf.heading || '').trim().toLowerCase();
    const node = [...page.querySelectorAll('article')].find(article => {
      const heading = article.querySelector('h2');
      return heading && heading.textContent.trim().toLowerCase().includes(headingNeedle);
    });
    mountNode(node, title, leaf.tab);
  }

  function unitOptions(selected='') {
    const units = tree.snapshot?.units || [];
    return units.map(unit => `<option value="${esc(unit.symbol)}" ${String(unit.symbol)===String(selected)?'selected':''}>${esc(unit.name)} — ${esc(unit.symbol)}</option>`).join('');
  }

  function areasCheckboxes(prefix, selected=[]) {
    const set = new Set((selected || []).map(String));
    const areas = tree.snapshot?.areas || [];
    if (!areas.length) return '<span class="tree-muted">Участки ещё не созданы.</span>';
    return areas.map(area => `<label class="tree-check"><input type="checkbox" data-tree-area="${esc(prefix)}" value="${area.id}" ${set.has(String(area.id))?'checked':''}/><span>${esc(area.name)}</span></label>`).join('');
  }

  function entityRowHtml(entityType, index) {
    const withAreas = ['meter','stock_item'].includes(entityType);
    return `<div class="tree-repeat-row" data-tree-entity-row="${index}">
      <div class="tree-repeat-head"><b>Позиция ${index + 1}</b><button type="button" class="tree-remove-row" data-tree-remove-row="${index}">Убрать</button></div>
      <label>Название<input data-tree-field="name" maxlength="180" placeholder="Введите название" /></label>
      <label>Сокращения и другие названия<input data-tree-field="aliases" maxlength="1000" placeholder="Через запятую, необязательно" /></label>
      <label>Единица измерения<select data-tree-field="unit">${unitOptions()}</select></label>
      ${withAreas ? `<div class="tree-field-group"><span>На каких участках используется</span><div class="tree-check-grid">${areasCheckboxes(String(index))}</div></div>` : ''}
    </div>`;
  }

  function renderEntityBatch(entityType, title) {
    const stage = document.getElementById('treeStage');
    stage.innerHTML = `
      <div class="tree-form-page">
        <div class="tree-form-note">Заполняйте только нужные поля. Единицы измерения берутся из справочника организации.</div>
        <div id="treeEntityRows">${entityRowHtml(entityType,0)}</div>
        <div class="tree-form-actions">
          <button type="button" data-tree-add-entity-row>Добавить ещё позицию</button>
          <button type="button" class="primary" data-tree-save-entity-batch>Сохранить всё</button>
        </div>
      </div>`;
    stage.dataset.entityType = entityType;
    setHeader(title, `Настроить организацию / ${title}`);
  }

  function renderUnits(title) {
    const units = tree.snapshot?.units || [];
    const stage = document.getElementById('treeStage');
    stage.innerHTML = `
      <div class="tree-form-page">
        <div class="tree-form-note">Этот список используется во всех формах позиций. Сначала добавьте нужные единицы, затем выбирайте их при создании сырья, изделий и других позиций.</div>
        <div class="tree-unit-create">
          <label>Название<input id="treeUnitName" maxlength="120" placeholder="Например: Мешки" /></label>
          <label>Обозначение<input id="treeUnitSymbol" maxlength="40" placeholder="Например: мешок" /></label>
          <button type="button" class="primary" data-tree-save-unit>Добавить единицу</button>
        </div>
        <h2 class="tree-subtitle">Созданные единицы</h2>
        <div class="tree-unit-list">${units.map(unit => `<div class="tree-unit-row"><span><b>${esc(unit.name)}</b><small>${esc(unit.symbol)}</small></span>${unit.is_default?'':'<button type="button" data-tree-archive-unit="'+unit.id+'">Скрыть</button>'}</div>`).join('')}</div>
      </div>`;
    setHeader(title, `Настроить организацию / ${title}`);
  }

  function renderAreaBatch(title) {
    const stage = document.getElementById('treeStage');
    stage.innerHTML = `
      <div class="tree-form-page">
        <div class="tree-form-note">Создайте один или несколько участков. После этого их можно привязать к площадкам и местам хранения.</div>
        <div id="treeAreaRows"><div class="tree-repeat-row" data-tree-area-row="0"><div class="tree-repeat-head"><b>Участок 1</b></div><label>Название<input data-tree-area-name maxlength="180" placeholder="Например: Фасовка" /></label></div></div>
        <div class="tree-form-actions"><button type="button" data-tree-add-area-row>Добавить ещё участок</button><button type="button" class="primary" data-tree-save-areas>Сохранить всё</button></div>
      </div>`;
    setHeader(title, `Настроить организацию / Структура / ${title}`);
  }

  function compositionRows(productId) {
    const components = tree.snapshot?.entities?.component || [];
    const existing = new Map((tree.snapshot?.compositions?.[String(productId)] || []).map(item => [Number(item.component_id), Number(item.quantity || 0)]));
    if (!components.length) return '<div class="tree-empty">Сначала добавьте комплектующие.</div>';
    return components.map(component => {
      const qty = existing.get(Number(component.id)) || 0;
      return `<div class="tree-component-row"><label><input type="checkbox" data-tree-component="${component.id}" ${qty>0?'checked':''}/><span><b>${esc(component.name)}</b><small>${esc(component.default_unit || 'шт')}</small></span></label><input type="number" min="0" step="any" inputmode="decimal" data-tree-component-qty="${component.id}" value="${qty>0?qty:''}" placeholder="Количество" /></div>`;
    }).join('');
  }

  function renderComposition(title) {
    const products = tree.snapshot?.entities?.product || [];
    const first = products[0]?.id || '';
    const stage = document.getElementById('treeStage');
    stage.innerHTML = `
      <div class="tree-form-page">
        <label>Изделие<select id="treeCompositionProduct"><option value="">Выберите изделие</option>${products.map(product=>`<option value="${product.id}">${esc(product.name)}</option>`).join('')}</select></label>
        <div class="tree-inline-actions"><button type="button" data-tree-select-components>Выбрать все</button><button type="button" data-tree-clear-components>Снять все</button></div>
        <div id="treeCompositionRows" class="tree-component-list">${first?compositionRows(first):'<div class="tree-empty">Сначала добавьте изделие.</div>'}</div>
        <button type="button" class="primary tree-save-wide" data-tree-save-composition>Сохранить состав</button>
      </div>`;
    const select = document.getElementById('treeCompositionProduct');
    if (select && first) select.value = String(first);
    setHeader(title, `Настроить организацию / ${title}`);
  }

  function renderAssignWorker(title) {
    const jobs = tree.snapshot?.job_titles || [];
    const users = tree.snapshot?.known_users || [];
    const stage = document.getElementById('treeStage');
    stage.innerHTML = `
      <div class="tree-form-page tree-narrow-form">
        <datalist id="treeKnownUsers">${users.flatMap(user => {
          const out=[];
          if(user.username)out.push(`<option value="@${esc(user.username)}">${esc(user.label||'')}</option>`);
          if(user.user_id)out.push(`<option value="${user.user_id}">${esc(user.label||'')}</option>`);
          return out;
        }).join('')}</datalist>
        <label>Telegram ID или @username<input id="treeWorkerRef" list="treeKnownUsers" placeholder="@username или 123456789" autocomplete="off" /></label>
        <label>Имя сотрудника<input id="treeWorkerName" maxlength="180" placeholder="Можно оставить пустым" /></label>
        <label>Должность<select id="treeWorkerJob"><option value="">Выберите созданную должность</option>${jobs.map(job=>`<option value="${job.id}">${esc(job.name)}</option>`).join('')}</select></label>
        <button type="button" class="primary tree-save-wide" data-tree-assign-worker>Назначить должность</button>
        <div class="tree-form-note">По Telegram ID назначение работает сразу. Для @username пользователь должен хотя бы один раз написать боту или в рабочую группу, чтобы система запомнила его ID.</div>
      </div>`;
    setHeader(title, `Настроить организацию / Сотрудники и права / ${title}`);
  }

  function renderCustom(leaf, title) {
    if (leaf.screen === 'units') return renderUnits(title);
    if (leaf.screen === 'entity-batch') return renderEntityBatch(leaf.entityType, title);
    if (leaf.screen === 'area-batch') return renderAreaBatch(title);
    if (leaf.screen === 'composition') return renderComposition(title);
    if (leaf.screen === 'assign-worker') return renderAssignWorker(title);
  }

  async function openLeaf(item) {
    if (!itemAllowed(item)) return;
    const leaf = item.leaf || {};
    if (leaf.type === 'custom') {
      await loadSnapshot(true);
      restoreMounted();
      renderCustom(leaf, item.title);
      return;
    }
    if (leaf.type === 'panel') return openLegacyPanel(leaf, item.title);
    return openLegacyPage(leaf, item.title);
  }

  function goBack() {
    restoreMounted();
    if (!tree.history.length) return renderMenu('root', false);
    const previous = tree.history.pop();
    tree.currentMenu = previous;
    renderMenu(previous, false);
  }

  function goHome() {
    restoreMounted();
    tree.history = [];
    tree.currentMenu = 'root';
    renderMenu('root', false);
  }

  async function saveUnit() {
    const name = document.getElementById('treeUnitName')?.value.trim() || '';
    const symbol = document.getElementById('treeUnitSymbol')?.value.trim() || '';
    if (!name || !symbol) return notify('Укажите название и обозначение.', true);
    try {
      const data = await postJson('/api/tree/units', {...contextBody(),name,symbol});
      tree.snapshot.units = data.units || tree.snapshot.units;
      notify(data.message || 'Единица добавлена.');
      renderUnits('Единицы измерения');
    } catch(error) { notify(error.message,true); }
  }

  async function archiveUnit(unitId) {
    try {
      const q = new URLSearchParams({chat_id:String(chatId),user_id:String(userId),unit_id:String(unitId)});
      const request = typeof apiFetch === 'function' ? apiFetch : fetch;
      const response = await request('/api/tree/units?'+q.toString(), {method:'DELETE',headers});
      const data = await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(data.detail||'Не удалось скрыть единицу.');
      tree.snapshot.units = data.units || tree.snapshot.units;
      notify(data.message || 'Единица скрыта.');
      renderUnits('Единицы измерения');
    } catch(error) { notify(error.message,true); }
  }

  function addEntityRow() {
    const box = document.getElementById('treeEntityRows');
    if (!box) return;
    const type = document.getElementById('treeStage')?.dataset.entityType || 'material';
    const index = box.querySelectorAll('[data-tree-entity-row]').length;
    box.insertAdjacentHTML('beforeend', entityRowHtml(type,index));
  }

  async function saveEntityBatch() {
    const stage = document.getElementById('treeStage');
    const type = stage?.dataset.entityType || '';
    const rows = [...document.querySelectorAll('[data-tree-entity-row]')];
    const items = rows.map((row,index) => ({
      name: row.querySelector('[data-tree-field="name"]')?.value.trim() || '',
      aliases: row.querySelector('[data-tree-field="aliases"]')?.value.trim() || '',
      unit: row.querySelector('[data-tree-field="unit"]')?.value || '',
      area_ids: [...row.querySelectorAll(`[data-tree-area="${index}"]:checked`)].map(input=>Number(input.value)),
    })).filter(item => item.name);
    if (!items.length) return notify('Добавьте хотя бы одну позицию.', true);
    try {
      const data = await postJson('/api/tree/entities/batch', {...contextBody(),entity_type:type,items});
      mergeSnapshot(data);
      notify(data.message || 'Позиции сохранены.');
      renderEntityBatch(type, `Добавить ${entityLabels[type]?.toLowerCase() || 'позиции'}`);
    } catch(error) { notify(error.message,true); }
  }

  function addAreaRow() {
    const box = document.getElementById('treeAreaRows');
    if (!box) return;
    const index = box.querySelectorAll('[data-tree-area-row]').length;
    box.insertAdjacentHTML('beforeend', `<div class="tree-repeat-row" data-tree-area-row="${index}"><div class="tree-repeat-head"><b>Участок ${index+1}</b><button type="button" class="tree-remove-row" data-tree-remove-area-row="${index}">Убрать</button></div><label>Название<input data-tree-area-name maxlength="180" placeholder="Название участка" /></label></div>`);
  }

  async function saveAreas() {
    const names = [...document.querySelectorAll('[data-tree-area-name]')].map(input=>input.value.trim()).filter(Boolean);
    if (!names.length) return notify('Добавьте хотя бы один участок.', true);
    try {
      for (const name of names) {
        await postJson('/api/extensions/area', {...contextBody(),name});
      }
      await loadSnapshot(true);
      notify(`Сохранено участков: ${names.length}.`);
      renderAreaBatch('Добавить участок');
    } catch(error) { notify(error.message,true); }
  }

  async function assignWorker() {
    const worker_ref = document.getElementById('treeWorkerRef')?.value.trim() || '';
    const display_name = document.getElementById('treeWorkerName')?.value.trim() || '';
    const job_title_id = Number(document.getElementById('treeWorkerJob')?.value || 0);
    if (!worker_ref || !job_title_id) return notify('Укажите Telegram ID или @username и выберите должность.', true);
    try {
      const data = await postJson('/api/tree/worker/assign', {...contextBody(),worker_ref,display_name,job_title_id});
      mergeSnapshot(data);
      notify(data.message || 'Должность назначена.');
      renderAssignWorker('Назначить должность');
    } catch(error) { notify(error.message,true); }
  }

  async function saveComposition() {
    const product_id = Number(document.getElementById('treeCompositionProduct')?.value || 0);
    if (!product_id) return notify('Выберите изделие.', true);
    const components = [...document.querySelectorAll('[data-tree-component]:checked')].map(check => {
      const id = Number(check.dataset.treeComponent);
      const quantity = Number(String(document.querySelector(`[data-tree-component-qty="${id}"]`)?.value || '').replace(',','.'));
      return {component_id:id,quantity};
    });
    if (components.some(item => !item.quantity || item.quantity <= 0)) return notify('Для каждой выбранной комплектующей укажите количество.',true);
    try {
      const data = await postJson('/api/tree/composition', {...contextBody(),product_id,components});
      mergeSnapshot(data);
      notify(data.message || 'Состав сохранён.');
      renderComposition('Состав изделия');
    } catch(error) { notify(error.message,true); }
  }

  function refreshCompositionRows() {
    const productId = Number(document.getElementById('treeCompositionProduct')?.value || 0);
    const box = document.getElementById('treeCompositionRows');
    if (box) box.innerHTML = productId ? compositionRows(productId) : '<div class="tree-empty">Выберите изделие.</div>';
  }

  function handleMenuClick(target) {
    const button = target.closest('[data-tree-item]');
    if (!button) return false;
    const item = findItem(tree.currentMenu, button.dataset.treeItem);
    if (!item || !itemAllowed(item)) return true;
    if (item.kind === 'menu') renderMenu(item.key, true);
    else openLeaf(item);
    return true;
  }

  document.addEventListener('click', event => {
    if (handleMenuClick(event.target)) return;
    if (event.target.closest('[data-tree-back]')) { goBack(); return; }
    if (event.target.closest('[data-tree-home]')) { goHome(); return; }
    if (event.target.closest('[data-tree-save-unit]')) { saveUnit(); return; }
    const archive = event.target.closest('[data-tree-archive-unit]'); if (archive) { archiveUnit(archive.dataset.treeArchiveUnit); return; }
    if (event.target.closest('[data-tree-add-entity-row]')) { addEntityRow(); return; }
    const removeRow = event.target.closest('[data-tree-remove-row]'); if (removeRow) { removeRow.closest('[data-tree-entity-row]')?.remove(); return; }
    if (event.target.closest('[data-tree-save-entity-batch]')) { saveEntityBatch(); return; }
    if (event.target.closest('[data-tree-add-area-row]')) { addAreaRow(); return; }
    const removeArea = event.target.closest('[data-tree-remove-area-row]'); if (removeArea) { removeArea.closest('[data-tree-area-row]')?.remove(); return; }
    if (event.target.closest('[data-tree-save-areas]')) { saveAreas(); return; }
    if (event.target.closest('[data-tree-assign-worker]')) { assignWorker(); return; }
    if (event.target.closest('[data-tree-save-composition]')) { saveComposition(); return; }
    if (event.target.closest('[data-tree-select-components]')) { document.querySelectorAll('[data-tree-component]').forEach(input=>input.checked=true); return; }
    if (event.target.closest('[data-tree-clear-components]')) { document.querySelectorAll('[data-tree-component]').forEach(input=>input.checked=false); return; }
  }, true);

  document.addEventListener('change', event => {
    if (event.target.id === 'treeAccountSelect') { switchAccount(event.target.value); return; }
    if (event.target.id === 'treeCompositionProduct') { refreshCompositionRows(); return; }
  });

  async function boot() {
    if (tree.booted) return;
    tree.booted = true;
    ensureShell();
    let attempts = 0;
    const wait = async () => {
      attempts += 1;
      renderContextSelector();
      const accounts = typeof state !== 'undefined' ? (state.accounts || []) : [];
      if (!chatId && accounts.length) chatId = String(accounts[0].scope_chat_id || '');
      if (chatId && userId) {
        const ok = await loadSnapshot(true);
        if (ok) {
          renderContextSelector();
          tree.history=[];
          tree.currentMenu='root';
          renderMenu('root',false);
          return;
        }
      }
      if (attempts < 30) setTimeout(wait, 250);
      else {
        const stage=document.getElementById('treeStage');
        if(stage)stage.innerHTML='<div class="tree-empty">Не удалось открыть доступный учёт. Закройте Mini App и откройте его снова из бота.</div>';
      }
    };
    wait();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true});
  else boot();
})();
