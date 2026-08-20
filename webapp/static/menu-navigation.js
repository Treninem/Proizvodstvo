// Hierarchical Mini App navigation: 20260820a
(() => {
  const groups = [
    {key:'main', title:'Главное', icon:'⌂', open:true, items:[
      {tab:'work', title:'Рабочий ввод', icon:'🏭', hint:'Внести текущую работу', work:true},
      {tab:'overview', title:'Склад и остатки', icon:'📦', hint:'Что есть и где находится'},
      {tab:'plan', title:'План', icon:'📋', hint:'План сборки', section:'assembly'},
      {tab:'reports', title:'Отчёты', icon:'📊', hint:'Результаты за период', section:'reports'},
    ]},
    {key:'production', title:'Производство', icon:'🏭', items:[
      {tab:'production', title:'Выпуск', icon:'⚙️', hint:'Произведённые детали и продукция', section:'production'},
      {tab:'materials', title:'Сырьё', icon:'🧱', hint:'Приход и расход материалов', section:'material'},
      {tab:'assembly', title:'Сборка', icon:'🧩', hint:'Сборка готового изделия', section:'assembly'},
      {tab:'movement', title:'Перемещение', icon:'↔️', hint:'Передача между участками и местами', section:'movement'},
      {tab:'shipment', title:'Отгрузка', icon:'🚚', hint:'Отправка продукции', section:'shipment'},
      {tab:'returns', title:'Возврат', icon:'↩️', hint:'Возврат продукции', section:'returns'},
      {tab:'inventory', title:'Инвентаризация', icon:'✅', hint:'Сверка фактических остатков', section:'stock'},
    ]},
    {key:'stock', title:'Склад', icon:'📦', items:[
      {tab:'overview', title:'Остатки и местонахождение', icon:'📍', hint:'Общий остаток и точное место'},
      {tab:'transfers', title:'Передачи', icon:'⇄', hint:'Отправить или принять'},
      {tab:'risks', title:'Критические остатки', icon:'⚠️', hint:'Что скоро закончится'},
      {tab:'places', title:'Места и направления', icon:'🏷️', hint:'Склады, клиенты и направления', section:'setup'},
      {tab:'organization', title:'Площадки и хранение', icon:'🏢', hint:'Площадки, участки и места хранения', admin:true},
    ]},
    {key:'management', title:'Планирование и управление', icon:'🗂️', items:[
      {tab:'plan', title:'План', icon:'📋', hint:'План сборки', section:'assembly'},
      {tab:'workflow', title:'Задания и заявки', icon:'🧾', hint:'Задачи, заявки, партии'},
      {tab:'shifts', title:'Смены', icon:'🕐', hint:'График и передача смены'},
      {tab:'inbox', title:'Входящие', icon:'🔔', hint:'Что требует внимания'},
      {tab:'control', title:'Контроль смены', icon:'🎛️', hint:'Сводка руководителя', control:true},
    ]},
    {key:'quality', title:'Качество и оборудование', icon:'✅', items:[
      {tab:'quality', title:'Качество и снабжение', icon:'🔎', hint:'Проверки, карантин, пополнение'},
      {tab:'workflow', title:'Оборудование', icon:'⚙️', hint:'Простои и обслуживание', focus:'workflowEquipmentBlock', equipment:true},
    ]},
    {key:'people', title:'Сотрудники и доступ', icon:'👥', items:[
      {tab:'team', title:'Сотрудники и должности', icon:'👤', hint:'Должности, права и назначения', section:'workers'},
      {tab:'departments', title:'Отделы', icon:'🏢', hint:'Отделы и руководители', department:true},
      {tab:'area-access', title:'Права доступа', icon:'🔐', hint:'Что кому разрешено', section:'permissions'},
    ]},
    {key:'setup', title:'Настройка', icon:'🛠️', items:[
      {tab:'catalog', title:'Справочники и составы', icon:'🗃️', hint:'Участки, позиции, состав изделия', section:'setup'},
      {tab:'organization', title:'Площадки и места хранения', icon:'📍', hint:'Города, площадки, склады', admin:true},
      {tab:'places', title:'Места и направления', icon:'🏷️', hint:'Хранение, клиенты, отправка', section:'setup'},
      {tab:'security', title:'Администрирование', icon:'🛡️', hint:'Резерв, устройства и история', section:'permissions', admin:true},
      {tab:'control', title:'Проверка работы системы', icon:'🩺', hint:'Состояние основных частей', focus:'controlDiagnosticsBlock', control:true},
    ]},
    {key:'help', title:'Помощь', icon:'❔', items:[
      {tab:'help', title:'Как пользоваться', icon:'📖', hint:'Пошаговая инструкция'},
    ]},
  ];

  const titleByTab = {
    overview:'Склад и остатки', work:'Рабочий ввод', production:'Выпуск', materials:'Сырьё', assembly:'Сборка',
    movement:'Перемещение', inventory:'Инвентаризация', transfers:'Передачи', risks:'Критические остатки',
    shipment:'Отгрузка', returns:'Возврат', plan:'План', reports:'Отчёты', workflow:'Задания и заявки',
    shifts:'Смены', inbox:'Входящие', control:'Контроль смены', quality:'Качество и снабжение', team:'Сотрудники и должности',
    departments:'Отделы', 'area-access':'Права доступа', organization:'Площадки и хранение', places:'Места и направления',
    security:'Администрирование', catalog:'Справочники и составы', help:'Как пользоваться'
  };

  function allowed(item) {
    try {
      if (item.admin && typeof state !== 'undefined' && !state.is_system_admin) return false;
      if (item.department && typeof state !== 'undefined' && !state.can_manage_departments) return false;
      if (item.control && typeof state !== 'undefined' && !(state.is_system_admin || state.can_manage_departments)) return false;
      if (item.work && typeof state !== 'undefined' && !(state.work_access || []).length) return false;
      if (item.equipment && typeof state !== 'undefined') {
        const manager = !!(state.is_system_admin || state.can_manage_departments);
        if (!manager && !(state.workflow?.equipment || []).length) return false;
      }
      if (item.section && typeof can === 'function' && !can(item.section)) return false;
    } catch (e) {}
    return true;
  }

  function createItem(item) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'app-menu-item';
    button.dataset.appNavTab = item.tab;
    button.dataset.appSearch = `${item.title} ${item.hint || ''}`.toLowerCase();
    if (item.focus) button.dataset.appNavFocus = item.focus;
    button.innerHTML = `<span class="app-menu-icon">${item.icon}</span><span><b>${item.title}</b><small>${item.hint || ''}</small></span><i>›</i>`;
    return button;
  }

  function createGroup(group) {
    const details = document.createElement('details');
    details.className = 'app-menu-group';
    details.dataset.menuGroup = group.key;
    details.open = !!group.open;
    const summary = document.createElement('summary');
    summary.innerHTML = `<span>${group.icon}</span><b>${group.title}</b><i>⌄</i>`;
    details.appendChild(summary);
    const body = document.createElement('div');
    body.className = 'app-menu-submenu';
    group.items.forEach(item => body.appendChild(createItem(item)));
    details.appendChild(body);
    return details;
  }

  function installMenu() {
    if (document.getElementById('appMenuDialog')) return;
    const shell = document.querySelector('main.app-shell') || document.querySelector('main');
    if (!shell) return;

    const bar = document.createElement('section');
    bar.className = 'app-nav-bar';
    bar.innerHTML = `
      <button type="button" class="app-nav-menu" data-app-menu-open><span>☰</span><b>Меню</b></button>
      <div class="app-nav-current"><small>Открыт раздел</small><strong id="appCurrentSection">Обзор</strong></div>
      <button type="button" class="app-nav-overview" data-app-nav-tab="overview"><span>⌂</span><small>Обзор</small></button>`;
    const cards = shell.querySelector('.cards');
    const hero = shell.querySelector('.hero');
    if (cards) cards.after(bar);
    else if (hero) hero.after(bar);
    else shell.prepend(bar);

    const dialog = document.createElement('dialog');
    dialog.id = 'appMenuDialog';
    dialog.className = 'app-menu-dialog';
    dialog.innerHTML = `
      <div class="app-menu-shell">
        <header><div><span class="app-menu-kicker">Производство</span><h2>Меню</h2><p>Выберите группу и нужное действие.</p></div><button type="button" data-app-menu-close aria-label="Закрыть">×</button></header>
        <label class="app-menu-search"><span>Найти раздел</span><input type="search" placeholder="Например: склад, должность, отчёт" autocomplete="off" /></label>
        <div class="app-menu-groups"></div>
      </div>`;
    const holder = dialog.querySelector('.app-menu-groups');
    groups.forEach(group => holder.appendChild(createGroup(group)));
    document.body.appendChild(dialog);
    refreshVisibility();
    updateCurrent();
  }

  function refreshVisibility() {
    groups.forEach(group => {
      const node = document.querySelector(`[data-menu-group="${group.key}"]`);
      if (!node) return;
      let count = 0;
      const buttons = [...node.querySelectorAll('.app-menu-item')];
      buttons.forEach((button, index) => {
        const visible = allowed(group.items[index]);
        button.classList.toggle('app-menu-item-hidden', !visible);
        if (visible) count += 1;
      });
      node.classList.toggle('app-menu-group-hidden', count === 0);
    });
  }

  function filterMenu(value) {
    const needle = String(value || '').trim().toLowerCase();
    document.querySelectorAll('.app-menu-group').forEach(group => {
      let matches = 0;
      group.querySelectorAll('.app-menu-item').forEach(button => {
        const available = !button.classList.contains('app-menu-item-hidden');
        const match = !needle || String(button.dataset.appSearch || '').includes(needle);
        button.classList.toggle('app-menu-search-hidden', !(available && match));
        if (available && match) matches += 1;
      });
      group.classList.toggle('app-menu-search-group-hidden', matches === 0);
      if (needle && matches) group.open = true;
    });
  }

  function activeTab() {
    const page = document.querySelector('.tab-page.active');
    return page?.id?.replace(/^page-/, '') || localStorage.getItem('prodMiniTab') || 'overview';
  }

  function updateCurrent() {
    const tab = activeTab();
    const label = document.getElementById('appCurrentSection');
    if (label) label.textContent = titleByTab[tab] || document.querySelector(`#page-${CSS.escape(tab)} h2`)?.textContent || 'Раздел';
    document.querySelectorAll('[data-app-nav-tab]').forEach(button => button.classList.toggle('active', button.dataset.appNavTab === tab));
  }

  function closeMenu() {
    const dialog = document.getElementById('appMenuDialog');
    if (dialog?.open) dialog.close();
  }

  function openMenu() {
    const dialog = document.getElementById('appMenuDialog');
    if (!dialog) return;
    refreshVisibility();
    filterMenu(dialog.querySelector('input[type="search"]')?.value || '');
    if (!dialog.open) dialog.showModal();
    setTimeout(() => dialog.querySelector('input[type="search"]')?.focus(), 30);
  }

  function navigate(button) {
    const tab = button.dataset.appNavTab;
    if (!tab || typeof showTab !== 'function') return;
    showTab(tab);
    closeMenu();
    updateCurrent();
    const focus = button.dataset.appNavFocus;
    if (focus) setTimeout(() => document.getElementById(focus)?.scrollIntoView({behavior:'smooth', block:'start'}), 80);
  }

  document.addEventListener('click', event => {
    if (event.target.closest('[data-app-menu-open]')) { openMenu(); return; }
    if (event.target.closest('[data-app-menu-close]')) { closeMenu(); return; }
    const nav = event.target.closest('[data-app-nav-tab]');
    if (nav) { event.preventDefault(); navigate(nav); }
  });

  document.addEventListener('input', event => {
    if (event.target.closest('.app-menu-search')) filterMenu(event.target.value);
  });

  document.addEventListener('click', event => {
    const dialog = document.getElementById('appMenuDialog');
    if (event.target === dialog) closeMenu();
  });

  installMenu();
  document.addEventListener('DOMContentLoaded', installMenu, {once:true});
  const observer = new MutationObserver(() => updateCurrent());
  setTimeout(() => {
    installMenu();
    document.querySelectorAll('.tab-page').forEach(page => observer.observe(page, {attributes:true, attributeFilter:['class']}));
    updateCurrent();
  }, 500);
})();
