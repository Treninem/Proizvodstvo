// Tree leaf refinement: keep one action or one list on screen at a time.
(() => {
  'use strict';

  let local = null;

  const refinements = {
    inventory: {
      title:'Инвентаризация',
      tab:'inventory',
      items:[
        ['Фактические остатки','Внести результат пересчёта','Фактические остатки'],
        ['Остатки по площадкам','Посмотреть текущие остатки','Остатки по площадкам'],
        ['История позиции','История изменений выбранной позиции','История выбранной позиции'],
        ['Массовая инвентаризация','Пересчитать сразу несколько позиций','Массовая инвентаризация','manage'],
        ['Позиции текущего пересчёта','Что уже внесено в пересчёт','Позиции текущего пересчёта','manage'],
        ['Сессии инвентаризации','Черновики и завершённые пересчёты','Сессии инвентаризации','manage'],
      ],
    },
    risks: {
      title:'Критические остатки',
      tab:'risks',
      items:[
        ['Красные флаги','Сводка текущих рисков','Красные флаги'],
        ['Внести фактические данные','Остаток или расход за период','Фактические данные'],
        ['Активные тревоги','Тревоги, которые требуют внимания','Активные тревоги'],
        ['Добавить событие','Поломка, задержка, больничный и другое','Событие или форс-мажор'],
        ['Активные события','События, влияющие на запас','Активные события'],
        ['Настроить правило','Пороги и способ расчёта','Правило критического остатка','manage'],
        ['Настроенные правила','Список правил критического остатка','Настроенные правила','manage'],
        ['Последние фактические данные','История введённых значений','Последние фактические данные'],
      ],
    },
    'reports-main': {
      title:'Отчёты',
      tab:'reports',
      items:[
        ['Сформировать отчёт','Excel или PDF за выбранный период','Отчёты'],
        ['Шаблоны отчётов','Сохранённые варианты отчёта','Сохранённые шаблоны'],
        ['Автоматическая отправка','Когда и куда отправлять отчёт','Автоматическая отправка'],
        ['Расписания отчётов','Список настроенных отправок','Расписания отчётов'],
        ['История доставки','Что и когда было отправлено','История доставки'],
        ['Импорт Excel','Загрузить существующую ведомость','Импорт Excel','owner'],
        ['Ведомости Excel','Готовые складские ведомости','Привычные ведомости Excel'],
      ],
    },
    transfers: {
      title:'Передачи',
      tab:'transfers',
      items:[
        ['Новая передача','Отправить позиции другому подразделению','Новая передача'],
        ['Входящие и исходящие','Принять, проверить или посмотреть передачу','Входящие и исходящие'],
      ],
    },
    destinations: {
      title:'Места и направления',
      tab:'places',
      items:[
        ['Добавить место или направление','Создать склад, клиента или точку отправки','Места хранения и направления'],
        ['Сохранённые места','Посмотреть и изменить созданные места','Сохранённые места'],
      ],
    },
    'organization-destinations': {
      title:'Места и направления',
      tab:'places',
      items:[
        ['Добавить место или направление','Создать склад, клиента или точку отправки','Места хранения и направления'],
        ['Сохранённые места','Посмотреть и изменить созданные места','Сохранённые места'],
      ],
    },
    departments: {
      title:'Отделы',
      tab:'departments',
      items:[
        ['Добавить или изменить отдел','Название и описание отдела','Отдел','manage'],
        ['Действия отдела','Какие операции разрешены отделу','Действия отдела','manage'],
        ['Позиции отдела','Какие позиции видит отдел','Позиции отдела','manage'],
        ['Доступ сотрудников','Добавить сотрудника в отдел','Доступ сотрудников'],
        ['Коды позиций','QR, штрихкод и внутренние коды','Коды позиций','manage'],
        ['Список отделов','Созданные отделы и настройки','Отделы ещё не созданы','manage','id:departmentList'],
      ],
    },
    'area-access': {
      title:'Права по участкам',
      tab:'area-access',
      items:[
        ['Настроить правило','Должность, участок и разрешённое действие','Доступ должностей по площадкам','manage'],
        ['Действующие правила','Посмотреть текущие ограничения','Действующие правила','manage'],
      ],
    },
    inbox: {
      title:'Входящие',
      tab:'inbox',
      items:[
        ['Входящие ответственного','Что требует внимания','Входящие ответственного'],
        ['Мои уведомления','Что присылать в Mini App и Telegram','Мои уведомления'],
      ],
    },
    control: {
      title:'Контроль смены',
      tab:'control',
      items:[
        ['Кто сейчас на смене','Открытые смены','Кто сейчас на смене'],
        ['Пакеты, требующие внимания','Непроверенные пакеты смен','Пакеты, требующие внимания'],
        ['Передача смены','Непринятые передачи','Передача смены'],
        ['Критические тревоги','Срочные проблемы','Критические тревоги'],
        ['Решения руководителей','Последние принятые решения','Последние решения руководителей'],
      ],
    },
    'owner-security': {
      title:'Администрирование',
      tab:'security',
      items:[
        ['Mini App и синхронизация','Состояние синхронизации','Mini App и синхронизация','owner'],
        ['Действия в Mini App','История действий','Действия в Mini App','owner'],
        ['Защита','Состояние защиты','Защита','owner'],
        ['Устройства Mini App','Устройства и локальные очереди','Устройства Mini App и синхронизация','owner'],
        ['Незавершённые смены','Настройка напоминаний','Контроль незавершённых смен','owner'],
        ['Чек-лист передачи смены','Обязательные пункты передачи','Чек-лист передачи смены','owner'],
        ['Скачать резервную копию','Копия выбранного учёта','Резерв','owner'],
        ['Восстановить учёт','Восстановление из резервной копии','Восстановление учёта','owner'],
      ],
    },
  };

  function esc(v){return String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}

  function allowed(item, ctx){
    const gate=item[3]||'';
    if(gate==='owner'&&!ctx.tree.access.is_primary_owner)return false;
    if(gate==='manage'&&!ctx.tree.access.can_manage)return false;
    return true;
  }

  function findArticle(tab, item){
    const page=document.getElementById(`page-${tab}`);
    if(!page)return null;
    const special=item[4]||'';
    if(special.startsWith('id:')){
      const child=document.getElementById(special.slice(3));
      return child?.closest('article')||null;
    }
    const needle=String(item[2]||'').trim().toLowerCase();
    return [...page.querySelectorAll('article')].find(article=>{
      const h=article.querySelector('h2,h3');
      return h&&h.textContent.trim().toLowerCase().includes(needle);
    })||null;
  }

  function renderSubmenu(key, cfg, ctx){
    ctx.restoreMounted();
    local={key,cfg,ctx,mode:'menu'};
    const stage=document.getElementById('treeStage');
    if(!stage)return;
    const items=cfg.items.filter(item=>allowed(item,ctx));
    stage.innerHTML=`<div class="tree-menu-intro"><p>Выберите конкретное действие. После выбора этот экран закроется.</p></div><div class="tree-menu-list">${items.map((item,index)=>`<button type="button" class="tree-menu-card" data-tree-refine-item="${index}"><span><b>${esc(item[0])}</b><small>${esc(item[1])}</small></span><i>›</i></button>`).join('')}</div>`;
    ctx.setHeader(cfg.title, `${ctx.menus[ctx.tree.currentMenu]?.title||'Меню'} / ${cfg.title}`);
    window.scrollTo({top:0,behavior:'instant'});
  }

  function openRefinedItem(index){
    if(!local)return;
    const {cfg,ctx}=local;
    const visible=cfg.items.filter(item=>allowed(item,ctx));
    const item=visible[Number(index)];
    if(!item)return;
    ctx.activateLegacy(cfg.tab);
    const node=findArticle(cfg.tab,item);
    if(!node){ctx.notify('Этот экран не найден в текущей сборке.',true);return;}
    local.mode='leaf';
    ctx.mountNode(node,item[0],cfg.tab);
  }

  window.__treeOpenOverride=(item,ctx)=>{
    const cfg=refinements[item.key];
    if(!cfg)return false;
    renderSubmenu(item.key,cfg,ctx);
    return true;
  };

  document.addEventListener('click',event=>{
    const item=event.target.closest('[data-tree-refine-item]');
    if(item&&local){event.preventDefault();event.stopImmediatePropagation();openRefinedItem(item.dataset.treeRefineItem);return;}
    const back=event.target.closest('[data-tree-back]');
    if(back&&local?.mode==='leaf'){
      event.preventDefault();event.stopImmediatePropagation();
      const {key,cfg,ctx}=local;
      ctx.restoreMounted();
      renderSubmenu(key,cfg,ctx);
      return;
    }
    const home=event.target.closest('[data-tree-home]');
    if(home&&local){local=null;}
  },true);
})();
