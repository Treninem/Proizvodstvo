// Mini App naming extensions: 20260820a
(() => {
  const currentAccount = () => (state.accounts || []).find(acc => String(acc.scope_chat_id) === String(chatId));

  function ensureAccountRenameButton(){
    const title = byId('accountName');
    if(!title) return;
    let button = byId('renameCurrentAccountButton');
    if(!button){
      button = document.createElement('button');
      button.id = 'renameCurrentAccountButton';
      button.type = 'button';
      button.textContent = 'Переименовать учёт';
      button.dataset.extensionAction = 'rename-account';
      button.className = 'secondary';
      title.insertAdjacentElement('afterend', button);
    }
    button.classList.toggle('hidden', !state.can_manage || !currentAccount());
  }

  function enhanceStorageLocationRows(){
    const box = byId('storageLocationList');
    if(!box) return;
    const locations = state.company_structure?.storage_locations || [];
    const rows = [...box.querySelectorAll('.manager-row')];
    rows.forEach((row, index) => {
      const item = locations[index];
      if(!item || row.querySelector('[data-extension-storage-rename]')) return;
      const actions = document.createElement('div');
      actions.className = 'mini-actions';
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Переименовать';
      button.dataset.extensionStorageRename = String(item.id);
      actions.appendChild(button);
      row.appendChild(actions);
    });
  }

  async function renameCurrentAccount(){
    const account = currentAccount();
    if(!account){showNotice('Сначала выберите учёт.', true);return;}
    const name = window.prompt('Новое название учёта', account.name || '');
    if(name === null) return;
    const clean = String(name).trim();
    if(!clean){showNotice('Название не может быть пустым.', true);return;}
    try{
      const res = await apiFetch('/api/extensions/account/rename', {
        method:'POST', headers,
        body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),account_id:Number(account.id),name:clean})
      });
      const data = await res.json().catch(()=>({}));
      if(!res.ok){showNotice(data.detail || 'Учёт не переименован.', true);return;}
      account.name = clean;
      if(byId('accountName')) byId('accountName').textContent = clean;
      renderAccounts();
      showNotice(data.message || 'Учёт переименован.');
    }catch(error){
      showNotice(error?.message || 'Учёт не переименован.', true);
    }
  }

  async function renameStorageLocation(locationId){
    const item = (state.company_structure?.storage_locations || []).find(x => String(x.id) === String(locationId));
    if(!item){showNotice('Место хранения не найдено.', true);return;}
    const name = window.prompt('Новое название места хранения', item.name || '');
    if(name === null) return;
    const clean = String(name).trim();
    if(!clean){showNotice('Название не может быть пустым.', true);return;}
    try{
      const res = await apiFetch('/api/extensions/storage-location/rename', {
        method:'POST', headers,
        body:JSON.stringify({chat_id:Number(chatId),user_id:Number(userId),location_id:Number(item.id),name:clean})
      });
      const data = await res.json().catch(()=>({}));
      if(!res.ok){showNotice(data.detail || 'Место хранения не переименовано.', true);return;}
      state.company_structure = state.company_structure || {};
      state.company_structure.storage_locations = data.storage_locations || [];
      renderCompanyStructure();
      showNotice(data.message || 'Место хранения переименовано.');
    }catch(error){
      showNotice(error?.message || 'Место хранения не переименовано.', true);
    }
  }

  try {
    const baseRenderAccounts = renderAccounts;
    renderAccounts = function(){
      const result = baseRenderAccounts.apply(this, arguments);
      ensureAccountRenameButton();
      return result;
    };
  } catch(e) {}

  try {
    const baseRenderCompanyStructure = renderCompanyStructure;
    renderCompanyStructure = function(){
      const result = baseRenderCompanyStructure.apply(this, arguments);
      enhanceStorageLocationRows();
      return result;
    };
  } catch(e) {}

  try {
    const baseApplyAccess = applyAccess;
    applyAccess = function(){
      const result = baseApplyAccess.apply(this, arguments);
      ensureAccountRenameButton();
      return result;
    };
  } catch(e) {}

  document.addEventListener('click', event => {
    const accountButton = event.target.closest('[data-extension-action="rename-account"]');
    if(accountButton){event.preventDefault();renameCurrentAccount();return;}
    const storageButton = event.target.closest('[data-extension-storage-rename]');
    if(storageButton){event.preventDefault();renameStorageLocation(storageButton.dataset.extensionStorageRename);}
  });

  ensureAccountRenameButton();
  enhanceStorageLocationRows();
  setTimeout(() => {ensureAccountRenameButton();enhanceStorageLocationRows();}, 1000);
})();
