// Worker workplace assignment bridge for tree Mini App: step92
(() => {
  'use strict';

  const originalFetch = window.fetch.bind(window);
  let lastContext = null;
  let lastHeaders = {};
  let workplaces = [];
  let loadingPlaces = false;

  function jsonBody(init) {
    try {
      if (!init || typeof init.body !== 'string') return null;
      return JSON.parse(init.body);
    } catch (_) {
      return null;
    }
  }

  function requestUrl(input) {
    if (typeof input === 'string') return input;
    if (input && typeof input.url === 'string') return input.url;
    return '';
  }

  function headersObject(headers) {
    if (!headers) return {};
    if (headers instanceof Headers) return Object.fromEntries(headers.entries());
    if (Array.isArray(headers)) return Object.fromEntries(headers);
    return {...headers};
  }

  function selectedKeys() {
    return [...document.querySelectorAll('[data-step92-workplace]:checked')]
      .map(input => String(input.value || '').trim())
      .filter(Boolean);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'",'&#039;');
  }

  function setWrapHtml(wrap, signature, html) {
    if (wrap.dataset.step92Signature === signature) return;
    wrap.dataset.step92Signature = signature;
    wrap.innerHTML = html;
  }

  function renderPlaces() {
    const job = document.getElementById('treeWorkerJob');
    if (!job) return;
    let wrap = document.getElementById('treeWorkerPlacesStep92');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'treeWorkerPlacesStep92';
      wrap.className = 'tree-worker-places-step92';
      const button = document.querySelector('[data-tree-assign-worker]');
      if (button && button.parentNode) button.parentNode.insertBefore(wrap, button);
      else job.parentNode?.insertAdjacentElement('afterend', wrap);
    }
    if (loadingPlaces) {
      setWrapHtml(wrap, 'loading', '<div class="tree-form-note">Загружаем рабочие места…</div>');
      return;
    }
    if (!workplaces.length) {
      setWrapHtml(
        wrap,
        'empty',
        '<div class="tree-form-note">Сначала создайте участок и место хранения. Без рабочего места сотрудник не назначается.</div>',
      );
      return;
    }
    const previous = new Set(selectedKeys());
    const signature = `ready:${workplaces.map(place => String(place.key || '')).join('|')}:${[...previous].sort().join('|')}`;
    const html = `
      <div class="tree-form-note"><b>Рабочие места</b><br>Выберите одно или несколько. Если выбрано несколько, в групповом чате сотрудник будет выбирать место для каждой записи.</div>
      <div class="tree-worker-place-list">
        ${workplaces.map(place => {
          const key=String(place.key||'');
          const checked=previous.has(key)?' checked':'';
          return `<label class="tree-worker-place-option"><input type="checkbox" data-step92-workplace value="${escapeHtml(key)}"${checked}> <span>${escapeHtml(place.label||'Рабочее место')}</span></label>`;
        }).join('')}
      </div>
      <div class="actions tree-worker-place-actions">
        <button type="button" data-step92-select-all>Выбрать все</button>
        <button type="button" data-step92-clear-all>Снять все</button>
      </div>`;
    setWrapHtml(wrap, signature, html);
  }

  async function loadPlaces() {
    if (!lastContext || !document.getElementById('treeWorkerJob') || loadingPlaces) return;
    loadingPlaces = true;
    renderPlaces();
    try {
      const response = await originalFetch('/api/step92/workplaces', {
        method: 'POST',
        headers: {...lastHeaders, 'Content-Type': 'application/json'},
        body: JSON.stringify(lastContext),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить рабочие места.');
      workplaces = Array.isArray(data.workplaces) ? data.workplaces : [];
    } catch (error) {
      workplaces = [];
      const wrap = document.getElementById('treeWorkerPlacesStep92');
      if (wrap) {
        const message = escapeHtml(error.message || 'Не удалось загрузить рабочие места.');
        setWrapHtml(wrap, `error:${message}`, `<div class="tree-form-note">${message}</div>`);
      }
    } finally {
      loadingPlaces = false;
      renderPlaces();
    }
  }

  function scheduleEnhance() {
    queueMicrotask(() => {
      if (!document.getElementById('treeWorkerJob')) return;
      renderPlaces();
      if (!workplaces.length) void loadPlaces();
    });
  }

  window.fetch = async function step92Fetch(input, init = {}) {
    const url = requestUrl(input);
    const body = jsonBody(init);
    if (body && Number(body.chat_id) && Number(body.user_id)) {
      const nextContext = {chat_id:Number(body.chat_id), user_id:Number(body.user_id)};
      const changedContext = !lastContext
        || Number(lastContext.chat_id) !== nextContext.chat_id
        || Number(lastContext.user_id) !== nextContext.user_id;
      if (changedContext) workplaces = [];
      lastContext = nextContext;
      lastHeaders = headersObject(init.headers);
    }

    let nextInput = input;
    let nextInit = init;
    if (url.includes('/api/tree/worker/assign')) {
      const workplaceKeys = selectedKeys();
      if (!workplaceKeys.length) {
        return new Response(JSON.stringify({detail:'Выберите хотя бы одно рабочее место.'}), {
          status: 400,
          headers: {'Content-Type':'application/json'},
        });
      }
      const payload = {...(body || {}), workplace_keys: workplaceKeys};
      nextInput = '/api/step92/worker/assign';
      nextInit = {
        ...init,
        headers: {...headersObject(init.headers), 'Content-Type':'application/json'},
        body: JSON.stringify(payload),
      };
    }

    const response = await originalFetch(nextInput, nextInit);
    if (url.includes('/api/tree/snapshot')) scheduleEnhance();
    if (url.includes('/api/tree/worker/assign') && response.ok) {
      workplaces = [];
      setTimeout(scheduleEnhance, 0);
    }
    return response;
  };

  document.addEventListener('click', event => {
    const all = event.target.closest?.('[data-step92-select-all]');
    if (all) {
      event.preventDefault();
      document.querySelectorAll('[data-step92-workplace]').forEach(input => { input.checked = true; });
      return;
    }
    const none = event.target.closest?.('[data-step92-clear-all]');
    if (none) {
      event.preventDefault();
      document.querySelectorAll('[data-step92-workplace]').forEach(input => { input.checked = false; });
    }
  });

  const observer = new MutationObserver(mutations => {
    // Ignore mutations made inside our own rendered list. That prevents the
    // observer from scheduling itself forever while still detecting a new tree
    // assignment screen created by the main Mini App shell.
    const external = mutations.some(mutation => {
      const target = mutation.target;
      return !(target instanceof Element && target.closest('#treeWorkerPlacesStep92'));
    });
    if (external) scheduleEnhance();
  });
  observer.observe(document.documentElement, {subtree:true, childList:true});
  scheduleEnhance();
})();
