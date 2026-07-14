(() => {
  const state = {
    videoResults: [],
    selectedVideoPaths: new Set(),
    searchQuery: '',
    searchTimer: null,
    tagQuery: '',
    fallbackCatalogTags: [],
  };

  const bridge = {
    getCurrentView: () => '',
    getCatalogTags: () => state.fallbackCatalogTags,
    setCatalogTags: items => {
      state.fallbackCatalogTags = Array.isArray(items) ? items : [];
      return state.fallbackCatalogTags;
    },
    loadCatalogTags: async () => state.fallbackCatalogTags,
  };

  function configure(options = {}) {
    if (typeof options.getCurrentView === 'function') bridge.getCurrentView = options.getCurrentView;
    if (typeof options.getCatalogTags === 'function') bridge.getCatalogTags = options.getCatalogTags;
    if (typeof options.setCatalogTags === 'function') bridge.setCatalogTags = options.setCatalogTags;
    if (typeof options.loadCatalogTags === 'function') bridge.loadCatalogTags = options.loadCatalogTags;
  }

  function currentView() {
    return String(bridge.getCurrentView?.() || '');
  }

  function catalogItems() {
    const items = bridge.getCatalogTags?.();
    return Array.isArray(items) ? items : [];
  }

  function setCatalogItems(items) {
    const next = Array.isArray(items) ? items : [];
    const applied = bridge.setCatalogTags?.(next);
    state.fallbackCatalogTags = Array.isArray(applied) ? applied : next;
    return catalogItems();
  }

  async function refreshCatalogTags(options = {}) {
    const items = await bridge.loadCatalogTags?.(options);
    if (Array.isArray(items)) state.fallbackCatalogTags = items;
    return catalogItems();
  }

  function safeCall(name, ...args) {
    const fn = window[name];
    if (typeof fn === 'function') return fn(...args);
    return undefined;
  }

  function openGlobalTagsView() {
    safeCall('nav', 'tags', document.querySelector('[data-v="tags"]'));
  }

  async function createGlobalCatalogTag() {
    if (currentView() !== 'tags') {
      openGlobalTagsView();
    }
    const focusCreate = () => {
      const input = document.getElementById('tags-create-name');
      if (!input) return;
      if (input.value.trim()) {
        createCatalogTagFromManager();
        return;
      }
      input.focus();
      input.scrollIntoView({block: 'center', behavior: 'smooth'});
      safeCall('showToast', 'Введите название тега и выберите цвет');
    };
    setTimeout(focusCreate, currentView() === 'tags' ? 0 : 150);
  }

  async function loadTagsView(options = {}) {
    const {silent = false} = options;
    if (!silent) safeCall('hideInlineError', 'tags-error');
    try {
      await refreshCatalogTags({force: true});
      renderGlobalTagsManager();
    } catch (err) {
      if (!silent) safeCall('showInlineError', 'tags-error', err.message || 'Не удалось загрузить теги');
    }
  }

  function showGlobalTagsError(message) {
    if (currentView() === 'tags') {
      safeCall('showInlineError', 'tags-error', message);
    } else if (typeof window.showStorageProfileError === 'function') {
      window.showStorageProfileError(message);
    } else {
      safeCall('showToast', message, 'err');
    }
  }

  async function createCatalogTag({name, color = '#64748b'} = {}) {
    const cleanName = String(name || '').trim();
    if (!cleanName) return null;
    try {
      const data = await api.post('/api/tags', {name: cleanName, color, kind: 'user'});
      setCatalogItems(catalogItems().concat([data.tag]));
      safeCall('renderStorageProfileDetail');
      renderGlobalTagsManager();
      safeCall('renderWorkspaceTagControls');
      if (currentView() === 'queue') safeCall('renderWorkspaceListAndDetail');
      safeCall('showToast', 'Тег создан');
      return data.tag;
    } catch (err) {
      safeCall('showToast', err.message || 'Не удалось создать тег', 'err');
      return null;
    }
  }

  function globalTagManagerTagRow(tag) {
    const locked = tag.locked || tag.kind === 'status' || tag.kind === 'channel';
    const actions = locked
      ? '<span class="mono dim">служебный</span>'
      : `<input class="tag-color-input" type="color" data-tag-color-id="${Number(tag.id)}" value="${esc(tag.color || '#64748b')}" title="Цвет тега" onchange="updateCatalogTagColor(${Number(tag.id)}, this.value)">
         <button class="btn-mini" onclick="renameCatalogTag(${Number(tag.id)})">Название</button>
         <button class="btn-danger" onclick="disableCatalogTag(${Number(tag.id)})">Отключить</button>`;
    return `<tr>
      <td>${tagPill(tag, {locked})}</td>
      <td class="mono dim">${esc(tagKindLabel(tag.kind))}</td>
      <td class="mono dim ov">${esc(tag.slug || '—')}</td>
      <td class="mono dim ov">${esc(tag.description || '')}</td>
      <td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }

  function renderGlobalTagsManager() {
    const el = document.getElementById('tags-manager');
    if (!el) return;
    const tags = catalogItems();
    const q = String(state.tagQuery || '').trim().toLowerCase();
    const matchesTagQuery = tag => !q || [tag.name, tag.slug, tag.description, tag.kind].join(' ').toLowerCase().includes(q);
    const userTags = tags.filter(tag => tag.kind === 'user' && matchesTagQuery(tag));
    const channelTags = tags.filter(tag => tag.kind === 'channel' && matchesTagQuery(tag));
    const statusTags = tags.filter(tag => tag.kind === 'status' && matchesTagQuery(tag));
    const selectedCount = state.selectedVideoPaths.size;
    const assignOptions = tagOptionsHtml([], {assignableOnly: true, emptyLabel: 'Выберите тег'});
    const results = state.videoResults.length
      ? state.videoResults.map(item => {
          const selected = state.selectedVideoPaths.has(item.workspace_path);
          const title = item.title || item.file_name || item.workspace_path;
          return `<label class="tag-video-row">
            <input type="checkbox" data-path="${esc(item.workspace_path)}" ${selected ? 'checked' : ''} onchange="toggleTagManagerVideoSelection(this.dataset.path, this.checked)">
            <div class="tag-video-thumb">${videoThumb(item.workspace_path, title)}</div>
            <div class="tag-video-main">
              <b title="${esc(title)}">${esc(title)}</b>
              <span class="mono dim" title="${esc(item.workspace_path)}">${esc(workspaceDisplayPath(item.workspace_path))}</span>
              ${tagListPills(item.tags || [])}
            </div>
            <button type="button" class="btn-mini" data-path="${esc(item.workspace_path)}" data-title="${esc(title)}" onclick="event.preventDefault();openWebPlayer(this.dataset.path,{title:this.dataset.title||''})">Смотреть</button>
          </label>`;
        }).join('')
      : '<div class="empty compact">Начните писать название, путь или тег — видео появятся автоматически. Можно нажать «Случайные».</div>';
    el.innerHTML = `<div class="tags-manager">
      <div class="storage-tag-panel-head">
        <div>
          <div class="storage-section-title inline-title">Менеджер тегов</div>
          <div class="mono dim">Создавайте теги, ищите видео по всему workspace и назначайте теги выбранным роликам. Профили потом подключают эти теги.</div>
        </div>
        <div class="row-actions">
          <button class="btn-secondary" onclick="createGlobalCatalogTag()">Создать тег</button>
          <button class="btn-mini" onclick="reloadCatalogTagsForUi()">Обновить</button>
        </div>
      </div>
      <div class="tags-manager-grid">
        <div class="tags-list-box">
          <div class="tag-manager-create-row">
            <input id="tags-create-name" type="text" placeholder="Новый тег">
            <input id="tags-create-color" class="tag-color-input" type="color" value="#64748b" title="Цвет нового тега">
            <button class="btn-secondary" onclick="createCatalogTagFromManager()">Создать</button>
          </div>
          <div class="field" style="margin-top:10px">
            <label class="field-lbl">Поиск тегов</label>
            <input id="tags-manager-search" type="text" value="${esc(state.tagQuery)}" placeholder="Название, slug, тип…" oninput="onGlobalTagManagerSearchInput(this.value)">
          </div>
          <div class="field-lbl">Пользовательские теги</div>
          ${userTags.length ? `<table class="tbl compact"><tbody>${userTags.map(globalTagManagerTagRow).join('')}</tbody></table>` : '<div class="empty compact">Пользовательских тегов пока нет.</div>'}
          <div class="field-lbl" style="margin-top:12px">Channel-теги</div>
          ${channelTags.length ? tagListPills(channelTags) : '<div class="mono dim">Появятся автоматически при привязке YouTube к профилю.</div>'}
          <div class="field-lbl" style="margin-top:12px">Статусы</div>
          ${statusTags.length ? tagListPills(statusTags) : '<div class="mono dim">Системные статусы ещё не загружены.</div>'}
        </div>
        <div class="tags-video-box">
          <div class="storage-search-panel">
            <div>
              <div class="field-lbl">Добавить теги в видео</div>
              <input id="tag-manager-video-search" type="text" value="${esc(state.searchQuery)}" placeholder="Название, путь или тег…" oninput="onTagManagerVideoSearchInput(this.value)">
            </div>
            <button class="btn-secondary" onclick="loadRandomTagManagerVideos()">Случайные</button>
          </div>
          <div class="tags-assign-row">
            <select id="tag-manager-assign-tag">${assignOptions}</select>
            <button class="btn-secondary" ${selectedCount ? '' : 'disabled'} onclick="assignTagToSelectedVideos()">Добавить выбранным (${selectedCount})</button>
            <button class="btn-mini" ${selectedCount ? '' : 'disabled'} onclick="removeTagFromSelectedVideos()">Снять выбранным</button>
          </div>
          <div class="tag-video-results">${results}</div>
        </div>
      </div>
    </div>`;
  }

  async function createCatalogTagFromManager() {
    const nameInput = document.getElementById('tags-create-name');
    const colorInput = document.getElementById('tags-create-color');
    const tag = await createCatalogTag({
      name: nameInput?.value || '',
      color: colorInput?.value || '#64748b',
    });
    if (tag && nameInput) nameInput.value = '';
  }

  function onGlobalTagManagerSearchInput(value) {
    state.tagQuery = String(value || '');
    renderGlobalTagsManager();
  }

  async function reloadCatalogTagsForUi() {
    await refreshCatalogTags({force: true});
    renderGlobalTagsManager();
    safeCall('renderStorageProfileDetail');
    safeCall('renderWorkspaceTagControls');
    safeCall('renderWorkspaceListAndDetail');
    safeCall('showToast', 'Теги обновлены');
  }

  function catalogTagById(tagId) {
    return catalogItems().find(tag => Number(tag.id) === Number(tagId)) || null;
  }

  async function renameCatalogTag(tagId) {
    const tag = catalogTagById(tagId);
    if (!tag || tag.locked) return;
    const name = await openTextActionModal({
      title: 'Переименовать тег',
      label: 'Название тега',
      value: tag.name || '',
      confirmText: 'Сохранить',
      validate: value => value ? '' : 'Введите название тега.',
    });
    if (!name) return;
    try {
      const data = await api.patch(`/api/tags/${Number(tagId)}`, {name});
      setCatalogItems(catalogItems().map(item => Number(item.id) === Number(tagId) ? data.tag : item));
      renderGlobalTagsManager();
      safeCall('renderWorkspaceListAndDetail');
      safeCall('showToast', 'Тег обновлён');
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось изменить тег');
    }
  }

  async function recolorCatalogTag(tagId) {
    const tag = catalogTagById(tagId);
    if (!tag || tag.locked) return;
    const input = document.querySelector(`[data-tag-color-id="${Number(tagId)}"]`);
    if (input) {
      input.focus();
      input.click();
      return;
    }
    openGlobalTagsView();
    safeCall('showToast', 'Измените цвет через цветной квадрат в таблице тегов');
  }

  async function updateCatalogTagColor(tagId, color) {
    const tag = catalogTagById(tagId);
    if (!tag || tag.locked) return;
    try {
      const data = await api.patch(`/api/tags/${Number(tagId)}`, {color});
      setCatalogItems(catalogItems().map(item => Number(item.id) === Number(tagId) ? data.tag : item));
      renderGlobalTagsManager();
      safeCall('renderWorkspaceListAndDetail');
      safeCall('renderWorkspaceFilterControls');
      safeCall('showToast', 'Цвет тега обновлён');
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось изменить цвет тега');
    }
  }

  async function disableCatalogTag(tagId) {
    const tag = catalogTagById(tagId);
    if (!tag || tag.locked) return;
    if (!confirm(`Отключить тег «${tag.name}»? Он исчезнет из выбора, но история связей останется в базе.`)) return;
    try {
      await api.del(`/api/tags/${Number(tagId)}`);
      setCatalogItems(catalogItems().filter(item => Number(item.id) !== Number(tagId)));
      renderGlobalTagsManager();
      safeCall('renderWorkspaceListAndDetail');
      safeCall('showToast', 'Тег отключён');
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось отключить тег');
    }
  }

  async function searchTagManagerVideos() {
    try {
      const data = await api.get(`/api/catalog/videos/search?q=${encodeURIComponent(state.searchQuery)}&scope=all&limit=80`);
      state.videoResults = data.items || [];
      state.selectedVideoPaths = new Set(Array.from(state.selectedVideoPaths).filter(path => state.videoResults.some(item => item.workspace_path === path)));
      renderGlobalTagsManager();
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось найти видео');
    }
  }

  function onTagManagerVideoSearchInput(value) {
    state.searchQuery = String(value || '');
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(searchTagManagerVideos, 250);
  }

  async function loadRandomTagManagerVideos() {
    try {
      const data = await api.get('/api/catalog/videos/random?scope=all&limit=32');
      state.videoResults = data.items || [];
      state.selectedVideoPaths.clear();
      renderGlobalTagsManager();
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось загрузить случайные видео');
    }
  }

  function toggleTagManagerVideoSelection(workspacePath, checked) {
    if (checked) state.selectedVideoPaths.add(workspacePath);
    else state.selectedVideoPaths.delete(workspacePath);
    renderGlobalTagsManager();
  }

  async function assignTagToSelectedVideos() {
    const tagId = Number(document.getElementById('tag-manager-assign-tag')?.value || 0);
    if (!tagId) {
      safeCall('showToast', 'Выберите тег', 'err');
      return;
    }
    const paths = Array.from(state.selectedVideoPaths);
    if (!paths.length) return;
    try {
      let updated = 0;
      for (const path of paths) {
        const item = state.videoResults.find(row => row.workspace_path === path) || {};
        const ids = catalogTagIds(item.tags || [], {includeStatus: true});
        if (!ids.includes(tagId)) ids.push(tagId);
        await window.updateVideoCatalogTags(path, ids);
        updated += 1;
      }
      renderGlobalTagsManager();
      safeCall('renderWorkspaceListAndDetail');
      safeCall('showToast', `Тег добавлен к видео: ${updated}`);
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось добавить тег к видео');
    }
  }

  async function removeTagFromSelectedVideos() {
    const tagId = Number(document.getElementById('tag-manager-assign-tag')?.value || 0);
    if (!tagId) {
      safeCall('showToast', 'Выберите тег', 'err');
      return;
    }
    const paths = Array.from(state.selectedVideoPaths);
    if (!paths.length) return;
    try {
      let updated = 0;
      for (const path of paths) {
        const item = state.videoResults.find(row => row.workspace_path === path) || {};
        const ids = catalogTagIds(item.tags || [], {includeStatus: true}).filter(id => id !== tagId);
        await window.updateVideoCatalogTags(path, ids);
        updated += 1;
      }
      renderGlobalTagsManager();
      safeCall('renderWorkspaceListAndDetail');
      safeCall('showToast', `Тег снят с видео: ${updated}`);
    } catch (err) {
      showGlobalTagsError(err.message || 'Не удалось снять тег с видео');
    }
  }

  function syncCatalogVideoTags(workspacePath, tags, updatedItem = null) {
    if (!workspacePath) return;
    const nextTags = tags || [];
    state.videoResults = state.videoResults.map(item => {
      if (item.workspace_path !== workspacePath) return item;
      const next = {
        ...item,
        tags: nextTags,
        is_publish_ready: nextTags.some(tag => tag.slug === 'status-ready'),
      };
      if (updatedItem?.workspace_status) next.workspace_status = updatedItem.workspace_status;
      if (updatedItem?.title !== undefined) next.title = updatedItem.title || next.title;
      return next;
    });
    if (currentView() === 'tags') renderGlobalTagsManager();
  }

  window.ShortsFarmTags = {
    configure,
    loadTagsView,
    openGlobalTagsView,
    createGlobalCatalogTag,
    syncCatalogVideoTags,
  };
  Object.assign(window, {
    openGlobalTagsView,
    createGlobalCatalogTag,
    loadTagsView,
    createCatalogTagFromManager,
    onGlobalTagManagerSearchInput,
    reloadCatalogTagsForUi,
    renameCatalogTag,
    recolorCatalogTag,
    updateCatalogTagColor,
    disableCatalogTag,
    onTagManagerVideoSearchInput,
    loadRandomTagManagerVideos,
    toggleTagManagerVideoSelection,
    assignTagToSelectedVideos,
    removeTagFromSelectedVideos,
  });
})();
