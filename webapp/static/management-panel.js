// Mini App management parity: 20260820a
(() => {
  let catalogState = {areas:[], entities:{}, compositions:{}, job_titles:[], workers:[], known_users:[]};
  let catalogLoading = false;

  const esc = (value) => {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value ?? '').replace(/[&<>\"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[ch]));
  };

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

  function notify(message, bad=false) {
    if (typeof showNotice === 'function') showNotice(message, bad);
  }

  function currentContext() {
    return {chat_id:Number(chatId), user_id:Number(userId)};
  }

  function mergeSnapshot(data) {
    catalogState = {
      areas: data.areas || [],
      entities: data.entities || {},
      compositions: data.compositions || {},
      job_titles: data.job_titles || [],
      workers: data.workers || [],
      known_users: data.known_users || [],
    };
    if (typeof state !== 'undefined') {
      state.areas = catalogState.areas;
      state.entities = {...(state.entities || {}), ...catalogState.entities};
      state.job_titles = catalogState.job_titles;
      state.workers = catalogState.workers;
    }
    try { if (typeof fillForms === 'function') fillForms(); } catch(e) {}
    try { if (typeof renderTeam === 'function') renderTeam(); } catch(e) {}
    renderCatalog();
    renderKnownUsers();
  }

  async function loadCatalogSnapshot(force=false) {
    if (catalogLoading || !chatId || !userId) return;
    if (!force && catalogState.areas.length && Object.keys(catalogState.entities || {}).length) return;
    catalogLoading = true;
    try {
      const data = await postJson('/api/extensions/catalog/snapshot', currentContext());
      mergeSnapshot(data);
    } catch (error) {
      if (force) notify(error.message, true);
    } finally {
      catalogLoading = false;
    }
  }

  function optionHtml(items, placeholder='Выберите') {
    return `<option value="">${esc(placeholder)}</option>` + (items || []).map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
  }

  function ensureCatalogPage() {
    if (document.getElementById('page-catalog')) return;
    const main = document.querySelector('main.app-shell') || document.querySelector('main');
    if (!main) return;
    const page = document.createElement('section');
    page.className = 'tab-page';
    page.id = 'page-catalog';
    page.dataset.section = 'setup';
    page.innerHTML = `
      <section class="hub-head catalog-head">
        <div>
          <p class="eyebrow">настройка</p>
          <h2>Справочники и составы</h2>
          <p>Здесь можно настроить учёт без перехода в бот: участки, позиции, состав изделия и сотрудников.</p>
        </div>
        <button type="button" data-catalog-refresh>Обновить</button>
      </section>

      <div class="catalog-shortcuts">
        <button type="button" data-app-nav-tab="team"><span>👥</span><b>Должности и сотрудники</b><small>Права и назначения</small></button>
        <button type="button" data-app-nav-tab="organization"><span>📍</span><b>Площадки и хранение</b><small>Города, участки, склады</small></button>
        <button type="button" data-app-nav-tab="area-access"><span>🔐</span><b>Права по участкам</b><small>Кому что доступно</small></button>
        <button type="button" data-app-nav-tab="places"><span>🏷️</span><b>Места и направления</b><small>Склады, клиенты, отправка</small></button>
      </div>

      <div class="grid catalog-grid">
        <article class="panel catalog-card">
          <h2>Новый учёт</h2>
          <p>Создайте ещё один отдельный учёт прямо из Mini App.</p>
          <label>Название<input id="catalogAccountName" maxlength="180" placeholder="Например: Основное производство" /></label>
          <button class="primary" type="button" data-catalog-create-account>Создать учёт</button>
        </article>

        <article class="panel catalog-card">
          <h2>Участок</h2>
          <p>Добавьте цех, линию, фасовку или другую рабочую зону.</p>
          <label>Название<input id="catalogAreaName" maxlength="180" placeholder="Например: Фасовка" /></label>
          <button class="primary" type="button" data-catalog-create-area>Создать участок</button>
          <div id="catalogAreaList" class="catalog-existing"></div>
        </article>

        <article class="panel wide catalog-card">
          <h2>Новая позиция</h2>
          <p>Изделия, комплектующие, сырьё, складские позиции и счётчики создаются здесь.</p>
          <div class="three-col">
            <label>Тип
              <select id="catalogEntityType">
                <option value="product">Изделие</option>
                <option value="component">Комплектующая</option>
                <option value="material">Сырьё</option>
                <option value="stock_item">Складская позиция</option>
                <option value="meter">Счётчик</option>
              </select>
            </label>
            <label>Название<input id="catalogEntityName" maxlength="180" placeholder="Название" /></label>
            <label>Единица<input id="catalogEntityUnit" maxlength="40" value="шт" placeholder="шт, кг, м…" /></label>
          </div>
          <label>Другие названия и сокращения<input id="catalogEntityAliases" maxlength="1000" placeholder="Через запятую, необязательно" /></label>
          <div id="catalogEntityAreaBlock" class="catalog-area-block hidden">
            <b>На каких участках используется</b>
            <div id="catalogEntityAreas" class="catalog-check-grid"></div>
          </div>
          <button class="primary" type="button" data-catalog-create-entity>Создать позицию</button>
          <div id="catalogEntityCounts" class="catalog-existing"></div>
        </article>

        <article class="panel wide catalog-card">
          <div class="section-title-row"><div><h2>Состав изделия</h2><p>Выберите изделие, отметьте сколько угодно созданных комплектующих и укажите количество на одну штуку.</p></div></div>
          <label>Изделие<select id="catalogProduct"></select></label>
          <div class="actions"><button type="button" data-catalog-select-all>Выбрать все</button><button type="button" data-catalog-select-none>Снять все</button></div>
          <div id="catalogComponentList" class="catalog-component-list empty">Сначала создайте изделие и комплектующие.</div>
          <button class="primary" type="button" data-catalog-save-composition>Сохранить состав</button>
        </article>

        <article class="panel wide catalog-card">
          <h2>Назначить должность сотруднику</h2>
          <p>Введите Telegram ID или @username сотрудника, затем выберите одну из уже созданных должностей.</p>
          <div class="three-col">
            <label>Telegram ID или @username<input id="catalogWorkerRef" list="knownTelegramUsers" placeholder="@username или 123456789" autocomplete="off" /></label>
            <label>Имя, если нужно<input id="catalogWorkerName" maxlength="180" placeholder="Можно оставить пустым" /></label>
            <label>Должность<select id="catalogWorkerJob"></select></label>
          </div>
          <button class="primary" type="button" data-catalog-assign-worker>Назначить должность</button>
          <p class="catalog-hint">Если @username ещё не находится, сотруднику достаточно один раз написать в рабочий чат или боту. По Telegram ID назначение работает сразу.</p>
        </article>
      </div>`;
    const security = document.getElementById('page-security');
    if (security) main.insertBefore(page, security);
    else main.appendChild(page);
  }

  function enhanceAccountChooser() {
    const chooser = document.getElementById('accountChooser');
    if (!chooser || chooser.querySelector('[data-mini-account-create]')) return;
    const block = document.createElement('div');
    block.dataset.miniAccountCreate = '1';
    block.className = 'mini-account-create';
    block.innerHTML = `<label>Новый учёт<input id="quickAccountName" maxlength="180" placeholder="Название учёта" /></label><button type="button" data-quick-create-account>Создать</button>`;
    chooser.appendChild(block);
  }

  function enhanceWorkerForm() {
    const input = document.getElementById('workerUserId');
    if (!input || input.dataset.usernameReady === '1') return;
    input.dataset.usernameReady = '1';
    input.removeAttribute('inputmode');
    input.placeholder = '@username или 123456789';
    input.setAttribute('list', 'knownTelegramUsers');
    const label = input.closest('label');
    if (label) {
      const textNode = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
      if (textNode) textNode.textContent = 'Telegram ID или @username';
    }
    const paragraph = input.closest('.form-panel')?.querySelector('p');
    if (paragraph) paragraph.textContent = 'Введите Telegram ID или @username и выберите уже созданную должность.';
  }

  function ensureKnownUsersDatalist() {
    if (document.getElementById('knownTelegramUsers')) return;
    const list = document.createElement('datalist');
    list.id = 'knownTelegramUsers';
    document.body.appendChild(list);
  }

  function renderKnownUsers() {
    ensureKnownUsersDatalist();
    const list = document.getElementById('knownTelegramUsers');
    if (!list) return;
    list.innerHTML = (catalogState.known_users || []).flatMap(user => {
      const entries = [];
      if (user.username) entries.push(`<option value="@${esc(user.username)}">${esc(user.label || user.display_name || user.user_id)}</option>`);
      if (user.user_id) entries.push(`<option value="${user.user_id}">${esc(user.label || user.display_name || user.user_id)}</option>`);
      return entries;
    }).join('');
  }

  function renderCatalog() {
    const areaList = document.getElementById('catalogAreaList');
    if (areaList) {
      areaList.innerHTML = catalogState.areas.length
        ? `<b>Созданы:</b> ${catalogState.areas.map(x => esc(x.name)).join(', ')}`
        : 'Участков пока нет.';
    }

    const typeNames = {product:'Изделия',component:'Комплектующие',material:'Сырьё',stock_item:'Складские позиции',meter:'Счётчики'};
    const counts = document.getElementById('catalogEntityCounts');
    if (counts) {
      counts.innerHTML = Object.entries(typeNames).map(([key,label]) => `<span>${label}: <b>${(catalogState.entities[key] || []).length}</b></span>`).join('');
    }

    const product = document.getElementById('catalogProduct');
    if (product) {
      const previous = product.value;
      product.innerHTML = optionHtml(catalogState.entities.product || [], 'Выберите изделие');
      if ([...product.options].some(x => x.value === previous)) product.value = previous;
      else if (product.options.length > 1) product.selectedIndex = 1;
    }

    const workerJob = document.getElementById('catalogWorkerJob');
    if (workerJob) {
      const previous = workerJob.value;
      workerJob.innerHTML = optionHtml(catalogState.job_titles || [], 'Выберите должность');
      if ([...workerJob.options].some(x => x.value === previous)) workerJob.value = previous;
    }

    const areas = document.getElementById('catalogEntityAreas');
    if (areas) {
      areas.innerHTML = catalogState.areas.length
        ? catalogState.areas.map(area => `<label class="catalog-check"><input type="checkbox" value="${area.id}" data-catalog-entity-area /><span>${esc(area.name)}</span></label>`).join('')
        : '<span class="empty">Сначала создайте участок.</span>';
    }
    renderComposition();
    updateEntityAreaVisibility();
  }

  function renderComposition() {
    const list = document.getElementById('catalogComponentList');
    const productId = Number(document.getElementById('catalogProduct')?.value || 0);
    if (!list) return;
    const components = catalogState.entities.component || [];
    if (!productId || !components.length) {
      list.className = 'catalog-component-list empty';
      list.textContent = 'Сначала выберите изделие и убедитесь, что комплектующие созданы.';
      return;
    }
    const existing = new Map((catalogState.compositions?.[String(productId)] || []).map(x => [Number(x.component_id), Number(x.quantity || 0)]));
    list.className = 'catalog-component-list';
    list.innerHTML = components.map(component => {
      const quantity = existing.get(Number(component.id)) || 0;
      return `<div class="catalog-component-row">
        <label class="catalog-component-name"><input type="checkbox" data-catalog-component="${component.id}" ${quantity > 0 ? 'checked' : ''}/><span><b>${esc(component.name)}</b><small>${esc(component.default_unit || component.unit || 'шт')}</small></span></label>
        <label class="catalog-component-qty">На 1 изделие<input type="number" min="0" step="any" inputmode="decimal" data-catalog-quantity="${component.id}" value="${quantity > 0 ? quantity : ''}" placeholder="0" /></label>
      </div>`;
    }).join('');
  }

  function updateEntityAreaVisibility() {
    const type = document.getElementById('catalogEntityType')?.value || 'product';
    const block = document.getElementById('catalogEntityAreaBlock');
    if (block) block.classList.toggle('hidden', !['meter','stock_item'].includes(type));
    const unit = document.getElementById('catalogEntityUnit');
    if (unit && !unit.dataset.touched) {
      unit.value = type === 'material' ? 'кг' : (type === 'meter' ? 'ед.' : 'шт');
    }
  }

  async function createAccount(nameInputId) {
    const input = document.getElementById(nameInputId);
    const name = String(input?.value || '').trim();
    if (!name) { notify('Введите название учёта.', true); return; }
    try {
      const data = await postJson('/api/extensions/accounts/create', {user_id:Number(userId), name});
      if (input) input.value = '';
      notify(data.message || 'Учёт создан.');
      try { if (typeof loadAccounts === 'function') await loadAccounts(); } catch(e) {}
    } catch (error) { notify(error.message, true); }
  }

  async function createArea() {
    const input = document.getElementById('catalogAreaName');
    const name = String(input?.value || '').trim();
    if (!name) { notify('Введите название участка.', true); return; }
    try {
      const data = await postJson('/api/extensions/catalog/area', {...currentContext(), name});
      if (input) input.value = '';
      mergeSnapshot(data);
      notify(data.message || 'Участок создан.');
    } catch (error) { notify(error.message, true); }
  }

  async function createEntity() {
    const entityType = document.getElementById('catalogEntityType')?.value || '';
    const name = String(document.getElementById('catalogEntityName')?.value || '').trim();
    const defaultUnit = String(document.getElementById('catalogEntityUnit')?.value || '').trim() || 'шт';
    const aliases = String(document.getElementById('catalogEntityAliases')?.value || '').trim();
    if (!name) { notify('Введите название позиции.', true); return; }
    const areaIds = [...document.querySelectorAll('[data-catalog-entity-area]:checked')].map(x => Number(x.value)).filter(Boolean);
    try {
      const data = await postJson('/api/extensions/catalog/entity', {...currentContext(), entity_type:entityType, name, default_unit:defaultUnit, aliases, area_ids:areaIds});
      document.getElementById('catalogEntityName').value = '';
      document.getElementById('catalogEntityAliases').value = '';
      document.querySelectorAll('[data-catalog-entity-area]').forEach(x => x.checked = false);
      mergeSnapshot(data);
      notify(data.message || 'Позиция создана.');
    } catch (error) { notify(error.message, true); }
  }

  async function saveComposition() {
    const productId = Number(document.getElementById('catalogProduct')?.value || 0);
    if (!productId) { notify('Выберите изделие.', true); return; }
    const selected = [...document.querySelectorAll('[data-catalog-component]:checked')];
    const components = [];
    for (const checkbox of selected) {
      const id = Number(checkbox.dataset.catalogComponent || 0);
      const input = document.querySelector(`[data-catalog-quantity="${id}"]`);
      const quantity = Number(String(input?.value || '').replace(',', '.'));
      if (!quantity || quantity <= 0) {
        const name = checkbox.closest('.catalog-component-row')?.querySelector('b')?.textContent || 'комплектующей';
        notify(`Укажите количество для «${name}».`, true);
        input?.focus();
        return;
      }
      components.push({component_id:id, quantity});
    }
    if (!components.length && !window.confirm('Снять все комплектующие из состава этого изделия?')) return;
    try {
      const data = await postJson('/api/extensions/catalog/composition', {...currentContext(), product_id:productId, components});
      mergeSnapshot(data);
      notify(data.message || 'Состав сохранён.');
    } catch (error) { notify(error.message, true); }
  }

  async function assignWorker(refId='catalogWorkerRef', nameId='catalogWorkerName', jobId='catalogWorkerJob') {
    const ref = String(document.getElementById(refId)?.value || '').trim();
    const displayName = String(document.getElementById(nameId)?.value || '').trim();
    const job = Number(document.getElementById(jobId)?.value || 0);
    if (!ref) { notify('Введите Telegram ID или @username сотрудника.', true); return; }
    if (!job) { notify('Выберите созданную должность.', true); return; }
    try {
      const data = await postJson('/api/extensions/workers/assign', {...currentContext(), worker_ref:ref, display_name:displayName, job_title_id:job});
      if (typeof state !== 'undefined') {
        state.workers = data.workers || state.workers;
        state.job_titles = data.job_titles || state.job_titles;
      }
      try { if (typeof renderTeam === 'function') renderTeam(); } catch(e) {}
      const refInput = document.getElementById(refId); if (refInput) refInput.value = '';
      const nameInput = document.getElementById(nameId); if (nameInput) nameInput.value = '';
      notify(data.message || 'Должность назначена.');
      await loadCatalogSnapshot(true);
    } catch (error) { notify(error.message, true); }
  }

  function selectAllComposition(value) {
    document.querySelectorAll('[data-catalog-component]').forEach(checkbox => {
      checkbox.checked = value;
      const id = checkbox.dataset.catalogComponent;
      const input = document.querySelector(`[data-catalog-quantity="${id}"]`);
      if (value && input && !input.value) input.value = '1';
    });
  }

  function install() {
    ensureCatalogPage();
    enhanceAccountChooser();
    ensureKnownUsersDatalist();
    enhanceWorkerForm();
    renderCatalog();
  }

  document.addEventListener('click', (event) => {
    const oldWorkerSave = event.target.closest('[data-action="save-worker"]');
    if (oldWorkerSave) {
      event.preventDefault();
      event.stopImmediatePropagation();
      assignWorker('workerUserId','workerName','workerJob');
      return;
    }

    if (event.target.closest('[data-catalog-refresh]')) { loadCatalogSnapshot(true); return; }
    if (event.target.closest('[data-catalog-create-account]')) { createAccount('catalogAccountName'); return; }
    if (event.target.closest('[data-quick-create-account]')) { createAccount('quickAccountName'); return; }
    if (event.target.closest('[data-catalog-create-area]')) { createArea(); return; }
    if (event.target.closest('[data-catalog-create-entity]')) { createEntity(); return; }
    if (event.target.closest('[data-catalog-save-composition]')) { saveComposition(); return; }
    if (event.target.closest('[data-catalog-assign-worker]')) { assignWorker(); return; }
    if (event.target.closest('[data-catalog-select-all]')) { selectAllComposition(true); return; }
    if (event.target.closest('[data-catalog-select-none]')) { selectAllComposition(false); return; }

    const tabNode = event.target.closest('[data-tab],[data-app-nav-tab]');
    const tab = tabNode?.dataset.appNavTab || tabNode?.dataset.tab;
    if (tab === 'catalog' || tab === 'team') setTimeout(() => loadCatalogSnapshot(true), 40);
  }, true);

  document.addEventListener('change', (event) => {
    if (event.target?.id === 'catalogProduct') renderComposition();
    if (event.target?.id === 'catalogEntityType') updateEntityAreaVisibility();
  });

  document.addEventListener('input', (event) => {
    if (event.target?.id === 'catalogEntityUnit') event.target.dataset.touched = '1';
  });

  install();
  document.addEventListener('DOMContentLoaded', install, {once:true});
  setTimeout(() => {
    install();
    if (chatId && userId) loadCatalogSnapshot(false);
  }, 800);
})();
