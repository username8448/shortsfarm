(() => {
  const state = {
    workspaceRoot: null,
    currentPath: '',
    data: null,
    loading: false,
  };

  function getWorkspaceRoot() {
    return state.workspaceRoot;
  }

  function setWorkspaceRoot(value, options = {}) {
    const next = value || null;
    const changed = state.workspaceRoot !== next;
    state.workspaceRoot = next;
    if (options.resetPath || changed && options.resetOnChange) {
      state.currentPath = '';
    }
    return state.workspaceRoot;
  }

  function managedAbsolutePath(relativePath) {
    const root = String(state.workspaceRoot || '').replace(/\/+$/, '');
    const relative = String(relativePath || '').replace(/^\/+/, '');
    return relative ? `${root}/${relative}` : root;
  }

  async function loadManagedFiles(path = state.currentPath || '') {
    const setup = document.getElementById('files-setup');
    const manager = document.getElementById('files-manager');
    if (!setup || !manager) return;
    hideInlineError('files-error');
    state.loading = true;
    try {
      const settings = await api.get('/api/settings/workspace');
      if (
        state.workspaceRoot
        && settings.workspace_root
        && state.workspaceRoot !== settings.workspace_root
      ) {
        path = '';
        state.currentPath = '';
      }
      state.workspaceRoot = settings.workspace_root || null;
      if (!settings.workspace_root || !settings.exists) {
        manager.style.display = 'none';
        setup.style.display = 'block';
        setup.innerHTML = `<div class="empty"><div style="margin-bottom:12px">Рабочая папка ещё не настроена.</div><button class="btn-primary" onclick="openWorkspaceSettings()">Открыть настройки Workspace</button></div>`;
        return;
      }
      setup.style.display = 'none';
      manager.style.display = 'block';
      document.getElementById('files-root-path').textContent = settings.workspace_root;
      const data = await api.get(`/api/files?path=${encodeURIComponent(path || '')}`);
      state.currentPath = data.path || '';
      state.data = data;
      renderManagedFiles();
    } catch (err) {
      manager.style.display = 'block';
      setup.style.display = 'none';
      showInlineError('files-error', err.message || 'Не удалось загрузить workspace');
    } finally {
      state.loading = false;
    }
  }

  function renderManagedFiles() {
    const data = state.data || {path: '', breadcrumbs: [], items: []};
    const sidebar = document.getElementById('files-sidebar');
    const crumbs = document.getElementById('files-breadcrumbs');
    const list = document.getElementById('files-list');
    if (sidebar) {
      sidebar.innerHTML = `<div class="field-lbl">Рабочая папка</div>${WORKSPACE_SYSTEM_FOLDERS.map(name => {
        const active = data.path === name || data.path.startsWith(name + '/');
        return `<button class="files-side-link${active ? ' on' : ''}" onclick="loadManagedFiles('${name}')"><i class="ti ti-folder"></i><span>${workspaceFolderLabel(name)}</span><small class="mono dim">${name}</small></button>`;
      }).join('')}`;
    }
    if (crumbs) {
      crumbs.innerHTML = `<button class="crumb" onclick="loadManagedFiles('')">Рабочая папка</button>${(data.breadcrumbs || []).map(item => `<span class="mono dim">/</span><button class="crumb" data-path="${esc(item.path)}" onclick="loadManagedFiles(this.dataset.path)">${esc(workspaceFolderLabel(item.path, item.name))}</button>`).join('')}`;
    }
    if (!list) return;
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = '<div class="empty">Папка пуста. Создайте структуру или импортируйте видео.</div>';
      return;
    }
    list.innerHTML = `<table class="tbl files-table"><thead><tr><th>Тип</th><th>Имя</th><th>Размер</th><th>Изменён</th><th>Действия</th></tr></thead><tbody>${items.map(item => {
      const folder = item.type === 'folder';
      const system = folder && !item.path.includes('/') && WORKSPACE_SYSTEM_FOLDERS.includes(item.path);
      const icon = folder ? 'ti-folder' : item.media_type === 'video' ? 'ti-video' : item.media_type === 'image' ? 'ti-photo' : 'ti-file';
      const type = folder ? workspaceKindLabel(item.kind) : workspaceKindLabel(item.media_type);
      const displayName = system ? workspaceFolderLabel(item.path, item.name) : (item.display_name || item.name);
      const displayPath = workspaceDisplayPath(item.path);
      const nameCell = !folder && item.media_type === 'video'
        ? `<button class="link-video mono txt ov" data-path="${esc(item.path)}" title="${esc(item.path)}" onclick="openWebPlayer(this.dataset.path)">${esc(displayName)}</button>`
        : `<div class="mono txt">${esc(displayName)}</div>`;
      const open = folder
        ? `<button class="btn-mini" data-path="${esc(item.path)}" onclick="loadManagedFiles(this.dataset.path)">Открыть</button>`
        : '';
      const videoActions = !folder && item.media_type === 'video'
        ? `${webPlayerButton(item.path)}<button class="btn-mini" data-path="${esc(item.path)}" onclick="openManagedFileInQueue(this.dataset.path)">Показать клипы</button><button class="btn-mini" data-path="${esc(item.path)}" onclick="registerManagedSource(this.dataset.path)">Добавить как исходник</button>`
        : '';
      const mutations = system ? '' : `<button class="btn-mini" data-path="${esc(item.path)}" data-name="${esc(item.name)}" onclick="renameManagedItem(this.dataset.path,this.dataset.name)">Переименовать</button><button class="btn-mini" data-path="${esc(item.path)}" onclick="moveManagedItem(this.dataset.path)">Переместить</button><button class="btn-danger" data-path="${esc(item.path)}" data-folder="${folder ? '1' : '0'}" onclick="deleteManagedItem(this.dataset.path,this.dataset.folder==='1')">Удалить</button>`;
      return `<tr><td><span class="workspace-type ${folder ? 'segment' : 'clip'}"><i class="ti ${icon}"></i>&nbsp;${esc(type || 'файл')}</span></td><td>${nameCell}<div class="mono dim" title="${esc(item.path)}">${esc(displayPath)}</div>${folder ? `<div class="mono dim">${Number(item.children_count || 0)} объектов</div>` : ''}</td><td class="mono mid">${folder ? '—' : esc(formatFileSize(item.size))}</td><td class="mono dim">${esc(formatMtime(item.modified_at))}</td><td><div class="row-actions">${open}${videoActions}${mutations}</div></td></tr>`;
    }).join('')}</tbody></table>`;
  }

  function refreshManagedFiles() {
    loadManagedFiles(state.currentPath || '');
  }

  function managedFilesUp() {
    const path = state.currentPath || '';
    if (!path) return;
    const parts = path.split('/');
    parts.pop();
    loadManagedFiles(parts.join('/'));
  }

  async function createManagedFolder(kind = 'custom') {
    const labels = {custom:'папки', collection:'коллекции', project:'проекта'};
    const name = await openTextActionModal({
      title: `Новая ${labels[kind] || 'папка'}`,
      label: 'Название',
      placeholder: 'Введите название',
      confirmText: 'Создать',
      validate: value => value ? '' : 'Введите название.',
    });
    if (!name) return;
    try {
      await api.post('/api/files/folder', {
        parent_path: state.currentPath || '',
        name,
        kind,
      });
      showToast('Папка создана');
      refreshManagedFiles();
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось создать папку');
    }
  }

  async function renameManagedItem(path, currentName) {
    const name = await openTextActionModal({
      title: 'Переименовать',
      label: 'Новое имя',
      value: currentName || '',
      confirmText: 'Переименовать',
      validate: value => value ? '' : 'Введите новое имя.',
    });
    if (!name || name === currentName) return;
    try {
      await api.patch('/api/files/rename', {path, new_name: name});
      showToast('Workspace item переименован');
      refreshManagedFiles();
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось переименовать item');
    }
  }

  async function moveManagedItem(path) {
    hideInlineError('files-error');
    const picked = await pickLocalPath({
      kind: 'directory',
      title: 'Выберите целевую папку внутри workspace',
      errorId: 'files-error',
    });
    if (!picked) return;
    let target = '';
    try {
      target = workspaceRelativeFromAbsolute(picked);
    } catch (err) {
      showInlineError('files-error', err.message || 'Выберите папку внутри workspace_root.');
      return;
    }
    try {
      await api.post('/api/files/move', {source_path: path, target_folder: target});
      showToast('Workspace item перемещён');
      refreshManagedFiles();
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось переместить item');
    }
  }

  function workspaceRelativeFromAbsolute(path) {
    const root = String(state.workspaceRoot || '').replace(/\/+$/, '');
    const selected = String(path || '').replace(/\/+$/, '');
    if (!root) throw new Error('workspace_root не настроен.');
    if (selected === root) return '';
    if (!selected.startsWith(root + '/')) {
      throw new Error('Выберите папку внутри текущего workspace_root.');
    }
    return selected.slice(root.length + 1);
  }

  async function deleteManagedItem(path, folder = false) {
    let recursive = false;
    if (folder) {
      recursive = confirm(`Удалить папку ${path} вместе со всем содержимым?\n\nOK — recursive delete, Отмена — ничего не удалять.`);
      if (!recursive) return;
    } else if (!confirm(`Удалить файл ${path}?`)) {
      return;
    }
    try {
      await api.del(`/api/files?path=${encodeURIComponent(path)}&recursive=${recursive ? 'true' : 'false'}`);
      showToast('Workspace item удалён');
      refreshManagedFiles();
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось удалить item');
    }
  }

  async function importManagedSource() {
    hideInlineError('files-error');
    const sourcePath = await pickLocalPath({
      kind: 'file',
      title: 'Выберите видео для импорта в Исходники',
      errorId: 'files-error',
    });
    if (!sourcePath) return;
    const current = state.currentPath || '';
    const target = current === 'sources' || current.startsWith('sources/')
      ? current
      : 'sources';
    try {
      const data = await api.post('/api/files/import-source', {
        source_path: sourcePath,
        target_folder: target,
        mode: 'copy',
      });
      showToast(`Видео импортировано: ${data.name}`);
      loadManagedFiles(target);
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось импортировать видео');
    }
  }

  async function registerManagedSource(path) {
    try {
      const data = await api.post('/api/files/register-source', {path});
      showToast(`Видео добавлено как исходник #${data.video_id}`);
    } catch (err) {
      showInlineError('files-error', err.message || 'Не удалось добавить исходник');
    }
  }

  async function openManagedFileInQueue(path) {
    const absolute = managedAbsolutePath(path);
    setWorkspaceParentVideoFilter({
      sourcePath: absolute,
      title: path.split('/').pop() || path,
    });
    activateView('queue', document.querySelector('[data-v="queue"]'));
    setQueueSubView('clips');
    loadJobs();
    await loadClips();
    scrollQueueClipsIntoView();
  }

  window.ShortsFarmFiles = {
    getWorkspaceRoot,
    setWorkspaceRoot,
    loadManagedFiles,
  };

  Object.assign(window, {
    loadManagedFiles,
    refreshManagedFiles,
    managedFilesUp,
    createManagedFolder,
    renameManagedItem,
    moveManagedItem,
    deleteManagedItem,
    importManagedSource,
    registerManagedSource,
    openManagedFileInQueue,
  });
})();
