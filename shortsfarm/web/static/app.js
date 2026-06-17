function describeNetworkError(path, err) {
  const raw = String(err?.message || err || 'NetworkError').trim();
  const origin = window.location?.origin || 'http://127.0.0.1:8000';
  const hints = [
    `Браузер не смог обратиться к backend по адресу ${origin}${path}.`,
    `Детали браузера: ${raw}`,
  ];
  if (window.location?.protocol === 'file:') {
    hints.push('Страница открыта как локальный файл, а не через команду shortsfarm web.');
  } else {
    hints.push(`Откройте UI именно по адресу, который печатает ./run web, обычно ${origin}.`);
  }
  hints.push('Если ShortsFarm запущен в удалённой среде, контейнере, WSL или по SSH, запустите ./run web --host 0.0.0.0 и откройте проброшенный порт 8000.');
  return hints.join('\n');
}

const api = {
  async get(path) {
    let res;
    try {
      res = await fetch(path);
    } catch (err) {
      throw new Error(describeNetworkError(path, err));
    }
    if (!res.ok) throw await makeError(res);
    return res.json();
  },
  async post(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {})
      });
    } catch (err) {
      throw new Error(describeNetworkError(path, err));
    }
    if (!res.ok) throw await makeError(res);
    return res.json();
  },
  async patch(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {})
      });
    } catch (err) {
      throw new Error(describeNetworkError(path, err));
    }
    if (!res.ok) throw await makeError(res);
    return res.json();
  },
  async del(path) {
    let res;
    try {
      res = await fetch(path, {method: 'DELETE'});
    } catch (err) {
      throw new Error(describeNetworkError(path, err));
    }
    if (!res.ok) throw await makeError(res);
    return res.json();
  }
};

async function makeError(res) {
  try {
    const data = await res.json();
    const detail = data?.detail;
    let message = '';
    if (typeof detail === 'string') message = detail;
    else if (detail && typeof detail.message === 'string') message = detail.message;
    else if (typeof data?.message === 'string') message = data.message;
    else if (data?.error && typeof data.error.message === 'string') message = data.error.message;
    else if (typeof data?.error === 'string') message = data.error;
    return new Error(message || `HTTP ${res.status}`);
  } catch {
    const text = await res.text();
    return new Error(text || `HTTP ${res.status}`);
  }
}

function esc(value) {
  return String(value ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function fmtNum(value) { return Number(value || 0).toString(); }
function shortPath(value) {
  if (!value) return '—';
  const text = String(value);
  return text.length > 58 ? '…' + text.slice(-57) : text;
}
function formatFileSize(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const value = Number(bytes);
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}
function formatMtime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', {dateStyle: 'short', timeStyle: 'short'});
}
function thumbnailUrl(path) {
  return `/api/fs/thumbnail?path=${encodeURIComponent(path || '')}`;
}
function videoThumb(path, name='video') {
  return `<img class="video-thumb" loading="lazy" src="${esc(thumbnailUrl(path))}" alt="Миниатюра ${esc(name)}">`;
}
function mpvButton(path, label='MPV') {
  if (!path) return '<button class="btn-mini" disabled>MPV</button>';
  return `<button class="btn-mini" data-path="${esc(path)}" onclick="openVideoInMpv(this.dataset.path)">${esc(label)}</button>`;
}
function outputFolderButton(path, label='Папка') {
  if (!path) return '<button class="btn-mini" disabled>Папка</button>';
  return `<button class="btn-mini" data-path="${esc(path)}" onclick="goToOutputFolder(this.dataset.path)">${esc(label)}</button>`;
}
function percent(done, total, status) {
  if (status === 'done' || status === 'failed') return 100;
  if (!total) return status === 'running' ? 50 : 0;
  return Math.round((done || 0) * 100 / total);
}
function ruStatus(value) {
  return ({
    queued:'в очереди',
    running:'в работе',
    done:'готово',
    draft:'черновик',
    ready:'готово',
    uploaded:'загружено',
    failed:'ошибка',
    rendering:'рендерится',
    inbox:'входящие',
    reviewing:'на просмотре',
    reviewed:'просмотрено',
    skipped:'пропущено',
    ok:'готово',
    preview:'план',
    active:'активен',
    disconnected:'отключён',
    expired:'истёк',
    error:'ошибка'
  }[value] || value || '—');
}
function badgeClass(status) {
  return status === 'done' || status === 'reviewed' || status === 'ok' || status === 'active' || status === 'ready' || status === 'uploaded'
    ? 'b-ok'
    : status === 'failed' || status === 'error'
      ? 'b-err'
    : status === 'running' || status === 'queued' || status === 'reviewing'
        ? 'b-info'
        : status === 'rendering' || status === 'expired'
          ? 'b-warn'
          : 'b-dim';
}
function badge(status, pulse=false) {
  return `<span class="badge ${badgeClass(status)}">${pulse ? '<span class="dot pulse" style="background:currentColor"></span>' : ''}${esc(ruStatus(status))}</span>`;
}

function showError(target, err) {
  const el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return;
  el.innerHTML = `<div class="err-line">${esc(err?.message || err || 'Неизвестная ошибка')}</div>`;
}
function showInlineError(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'err-line';
  el.textContent = message || 'Неизвестная ошибка';
  el.style.display = 'block';
}
function showInlineOk(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'ok-line';
  el.textContent = message || 'Готово';
  el.style.display = 'block';
}
function hideInlineError(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = '';
  el.className = 'err-line';
  el.style.display = 'none';
}
function hideInlineOk(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = '';
  el.className = 'ok-line';
  el.style.display = 'none';
}
function showToast(message, kind='ok') {
  const toast = document.createElement('div');
  toast.className = `toast ${kind === 'err' ? 'toast-err' : 'toast-ok'}`;
  toast.textContent = message || (kind === 'err' ? 'Ошибка' : 'Готово');
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('on'));
  setTimeout(() => {
    toast.classList.remove('on');
    setTimeout(() => toast.remove(), 180);
  }, 2600);
}

let currentView = 'dashboard';
let currentPublishTab = 'youtube';
let secsVal = 60;
let splitMode = 'file';
const skipList = [];
let lastJobs = [];
let lastVideos = [];
let lastClips = [];
let workspaceFilter = 'all';
let selectedWorkspaceKeys = new Set();
let currentWorkspaceItemKey = null;
let lastOutputs = [];
let lastYoutubeAccounts = [];
let lastYoutubeProfiles = [];
let lastPublishJobs = [];
let lastReadyPublishClips = [];
let editingOAuthProfileId = null;
let oauthManualMode = 'json';
let publishJobFilter = 'all';
let selectedPublishJobIds = new Set();
let hiddenDonePublishJobIds = new Set();
let publishBatchSize = 3;
const workspaceYoutubeState = {
  selectedAccountId: null,
  busy: false,
};

const publishState = {
  selectedProfileId: null,
  selectedAccountId: null,
  selectedClipId: null,
  onboardingHint: '',
  busy: false,
};

const fsState = {
  currentPath: null,
  parentPath: null,
  selectedVideoPath: null,
  selectedFolderPath: null,
  selectedVideoInfo: null,
  mode: 'file',
  roots: [],
  loading: false,
  splitting: false,
  lastList: null
};

function nav(id, btn) {
  currentView = id;
  document.querySelectorAll('.v').forEach(el => el.classList.remove('on'));
  document.getElementById('v-' + id).classList.add('on');
  document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
  if (btn) btn.classList.add('on');
  if (id === 'dashboard') loadDashboard();
  if (id === 'split' && !fsState.currentPath) initFsBrowser();
  if (id === 'queue') loadJobs();
  if (id === 'videos') loadVideos();
  if (id === 'clips') loadClips();
  if (id === 'publish') loadPublishView();
  if (id === 'settings') loadSettingsView();
}

async function loadDashboard() {
  try {
    const data = await api.get('/api/status');
    const jobs = data.jobs || {};
    const clips = data.clips || {};
    const videos = data.videos_by_status || {};
    const runningJobs = jobs.running || 0;
    const queuedJobs = jobs.queued || 0;
    const queuedClips = clips.queued || 0;
    const failedClips = clips.failed || 0;

    document.getElementById('st-videos').textContent = fmtNum(data.videos_total);
    document.getElementById('st-segments').textContent = fmtNum(data.segments_total);
    document.getElementById('st-jobs').textContent = fmtNum(runningJobs + queuedJobs);
    document.getElementById('st-clips').textContent = fmtNum(queuedClips);
    document.getElementById('st-videos-sub').textContent = `${videos.inbox||0} входящие · ${videos.reviewed||0} просмотрено`;
    document.getElementById('st-jobs-sub').textContent = `${runningJobs} в работе · ${queuedJobs} в очереди`;
    document.getElementById('st-clips-sub').textContent = `${clips.done||0} готово · ${failedClips} ошибок`;
    document.getElementById('nav-jobs').textContent = runningJobs + queuedJobs || '';
    document.getElementById('nav-videos').textContent = data.videos_total || '';
    document.getElementById('nav-clips').textContent = queuedClips || '';
    document.getElementById('job-pulse').classList.toggle('pulse', runningJobs > 0);

    renderRunningBanner(data.latest_jobs || []);
    renderJobsTable('dash-jobs', data.latest_jobs || [], false);
    renderVideoStatusBars(videos, data.videos_total || 0);
    renderErrors(data.recent_errors || []);
    lastOutputs = data.latest_outputs || lastOutputs;
  } catch (err) {
    showError('dash-jobs', err);
  }
}

function renderRunningBanner(jobs) {
  const running = jobs.find(job => job.status === 'running');
  const el = document.getElementById('running-banner');
  if (!running) { el.innerHTML = ''; return; }
  const p = percent(running.done_items, running.total_items, running.status);
  el.innerHTML = `<div class="banner"><div class="banner-top"><div class="banner-left"><span class="dot pulse" style="background:var(--acc);box-shadow:0 0 7px var(--acc);"></span><span style="font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.05em;">Задача #${running.id} · В работе</span><span class="mono mid">${esc(running.type)}</span></div><span class="mono inf" style="font-size:12px;font-weight:700;">${p}%</span></div><div class="pbar"><div class="pf pf-info" style="width:${p}%"></div></div><div class="mono mid" style="margin-top:6px;">${esc(running.current_file || '—')} · ${running.done_items||0}/${running.total_items||'?'}</div></div>`;
}

function renderVideoStatusBars(counts, total) {
  const labels = [['inbox','входящие','b-dim'],['reviewing','на просмотре','b-info'],['reviewed','просмотрено','b-ok'],['skipped','пропущено','b-dim']];
  document.getElementById('video-status-bars').innerHTML = labels.map(([key, label, cls]) => {
    const n = counts[key] || 0;
    const width = total ? Math.round(n * 100 / total) : 0;
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span class="badge ${cls}" style="min-width:96px;justify-content:center">${label}</span><div class="pbar" style="flex:1"><div class="pf" style="width:${width}%;background:var(--acc)"></div></div><span class="mono txt" style="min-width:22px;text-align:right">${n}</span></div>`;
  }).join('') || '<div class="empty">Нет данных</div>';
}

function renderErrors(errors) {
  const el = document.getElementById('recent-errors');
  if (!errors.length) { el.innerHTML = '<div class="empty">Ошибок нет</div>'; return; }
  el.innerHTML = errors.map(error => `<div style="padding:10px 15px;border-bottom:1px solid var(--border)"><div style="display:flex;gap:6px;align-items:center;margin-bottom:4px"><span class="badge b-err" style="font-size:9px">${esc(error.kind)} #${esc(error.id)}</span><span class="mono dim">${esc(error.at || '')}</span></div><div style="font-size:11px;color:var(--mid);line-height:1.4">${esc(error.error || 'Ошибка без текста')}</div></div>`).join('');
}

async function loadFsRoots() {
  const data = await api.get('/api/fs/roots');
  fsState.roots = data.roots || [];
  renderFsRoots();
}

function renderFsRoots() {
  const el = document.getElementById('fs-roots');
  if (!el) return;
  if (!fsState.roots.length) {
    el.innerHTML = '<button class="btn-mini" disabled>Нет доступных корней</button>';
    return;
  }
  el.innerHTML = fsState.roots.map(root => {
    const active = fsState.currentPath === root.path ? ' on' : '';
    return `<button class="btn-mini${active}" data-path="${esc(root.path)}" onclick="openFolder(this.dataset.path)">${esc(root.label)}</button>`;
  }).join('');
}

async function initFsBrowser() {
  try {
    await loadFsRoots();
    const stored = localStorage.getItem('shortsfarm.fs.lastPath');
    const videosRoot = fsState.roots.find(root => root.label === 'Видео');
    const homeRoot = fsState.roots.find(root => root.label === 'Дом');
    const candidates = [stored, videosRoot?.path, homeRoot?.path, fsState.roots[0]?.path].filter(Boolean);
    for (const path of [...new Set(candidates)]) {
      if (await openFolder(path, {silent: true})) return;
    }
    showInlineError('fs-error', 'Не удалось открыть стартовую папку');
    renderFileBrowser({items: []});
  } catch (err) {
    showInlineError('fs-error', `Не удалось загрузить папки:\n${err.message || err}`);
    renderFileBrowser({items: []});
  }
}

async function openFolder(path, options={}) {
  if (!path) return false;
  fsState.loading = true;
  hideInlineError('fs-error');
  renderFileBrowser({loading: true, items: []});
  updateActionButtons();
  try {
    const data = await api.get(`/api/fs/list?path=${encodeURIComponent(path)}`);
    const previousPath = fsState.currentPath;
    fsState.currentPath = data.path;
    fsState.parentPath = data.parent;
    fsState.selectedFolderPath = data.path;
    fsState.lastList = data;
    if (previousPath !== data.path) {
      fsState.selectedVideoPath = null;
      fsState.selectedVideoInfo = null;
      const input = document.getElementById('split-path');
      if (input && splitMode === 'file') input.value = '';
    }
    localStorage.setItem('shortsfarm.fs.lastPath', data.path);
    renderFsRoots();
    renderBreadcrumb(data.path);
    renderFileBrowser(data);
    renderSelection();
    return true;
  } catch (err) {
    if (!options.silent) {
      showInlineError('fs-error', `Не удалось открыть папку:\n${err.message || err}`);
      renderFileBrowser({items: []});
    }
    return false;
  } finally {
    fsState.loading = false;
    updateActionButtons();
  }
}

function renderBreadcrumb(path) {
  const el = document.getElementById('fs-breadcrumb');
  const current = document.getElementById('fs-current-path');
  if (current) current.textContent = path || '—';
  if (!el || !path) return;
  const normalized = String(path);
  const parts = normalized.split('/').filter(Boolean);
  const crumbs = [`<button class="crumb" data-path="/" onclick="openFolder(this.dataset.path)">/</button>`];
  let acc = '';
  for (const part of parts) {
    acc += '/' + part;
    crumbs.push(`<span class="mono dim">/</span><button class="crumb" data-path="${esc(acc)}" onclick="openFolder(this.dataset.path)">${esc(part)}</button>`);
  }
  el.innerHTML = crumbs.join('');
}

function renderFileBrowser(data) {
  const el = document.getElementById('fs-browser');
  if (!el) return;
  if (data?.loading) {
    el.innerHTML = '<div class="empty">Открываю папку...</div>';
    return;
  }
  const items = data?.items || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty">В этой папке нет видео</div>';
    return;
  }
  el.innerHTML = `<table class="tbl"><thead><tr><th>Тип</th><th>Имя</th><th>Размер</th><th>Изменён</th><th>Действие</th></tr></thead><tbody>${items.map(item => {
    const isDir = item.type === 'dir';
    const selected = item.path === fsState.selectedVideoPath;
    const action = isDir
      ? `<button class="btn-mini" data-path="${esc(item.path)}" onclick="openFolder(this.dataset.path)">Открыть</button>`
      : `<div class="row-actions"><button class="btn-mini${selected ? ' on' : ''}" data-path="${esc(item.path)}" onclick="selectVideo(this.dataset.path)">Выбрать</button>${mpvButton(item.path)}</div>`;
    const name = isDir
      ? `<span class="mono txt ov" title="${esc(item.path)}">${esc(item.name)}</span>`
      : `<div class="video-name-cell">${videoThumb(item.path, item.name)}<span class="mono txt ov" title="${esc(item.path)}">${esc(item.name)}</span></div>`;
    return `<tr><td class="mono ${isDir ? 'inf' : 'warn'} fs-type">${isDir ? 'Папка' : 'Видео'}</td><td>${name}</td><td class="mono mid">${esc(formatFileSize(item.size))}</td><td class="mono dim">${esc(formatMtime(item.mtime))}</td><td>${action}</td></tr>`;
  }).join('')}</tbody></table>`;
}

function goParentFolder() {
  if (fsState.parentPath) openFolder(fsState.parentPath);
}

function refreshFolder() {
  if (fsState.currentPath) openFolder(fsState.currentPath);
}

async function selectVideo(path) {
  fsState.selectedVideoPath = path;
  fsState.selectedVideoInfo = null;
  hideInlineError('split-error');
  const input = document.getElementById('split-path');
  if (input) input.value = path;
  renderSelection();
  updateActionButtons();
  try {
    const info = await api.get(`/api/fs/video-info?path=${encodeURIComponent(path)}`);
    fsState.selectedVideoInfo = info;
    renderSelection();
  } catch (err) {
    showInlineError('split-error', `Видео выбрано, но не удалось получить длительность:\n${err.message || err}`);
  }
}

async function openVideoInMpv(path) {
  if (!path) return;
  hideInlineError('split-error');
  try {
    await api.post('/api/fs/open-mpv', {path});
    showToast(`Открываю в MPV: ${shortPath(path)}`);
    if (currentView === 'split') showInlineOk('split-error', `Открываю в MPV: ${shortPath(path)}`);
  } catch (err) {
    const message = `Не удалось открыть MPV:\n${err.message || err}`;
    showToast(err.message || 'Не удалось открыть MPV', 'err');
    if (currentView === 'split') showInlineError('split-error', message);
  }
}

async function goToOutputFolder(path) {
  if (!path) {
    showToast('Папка сегментов пока неизвестна', 'err');
    return;
  }
  nav('split', document.querySelector('[data-v=split]'));
  setMode('folder');
  const ok = await openFolder(path);
  if (ok) {
    showToast(`Открыта папка сегментов: ${shortPath(path)}`);
    showInlineOk('fs-error', `Открыта папка сегментов:\n${path}`);
  }
}

function manualPathChanged() {
  const input = document.getElementById('split-path');
  const value = input?.value.trim() || '';
  if (value && value !== fsState.selectedVideoPath) {
    fsState.selectedVideoPath = null;
    fsState.selectedVideoInfo = null;
  }
  renderSelection();
  updateActionButtons();
}

function getManualPath() {
  return document.getElementById('split-path')?.value.trim() || '';
}

function getSelectedSplitPath() {
  const manual = getManualPath();
  if (splitMode === 'file') return fsState.selectedVideoPath || manual;
  return manual || fsState.currentPath;
}

function renderSelection() {
  const el = document.getElementById('selection-card');
  if (!el) return;
  const manual = getManualPath();
  if (splitMode === 'folder') {
    const path = manual || fsState.currentPath;
    if (!path) {
      el.innerHTML = '<div class="empty">Откройте папку для нарезки</div>';
      return;
    }
    el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Выбрана папка</div><div class="selection-name" title="${esc(path)}">${esc(shortPath(path))}</div><div class="selection-meta mono">Текущая открытая папка будет источником для нарезки</div></div>`;
    return;
  }
  if (fsState.selectedVideoPath) {
    const info = fsState.selectedVideoInfo;
    const name = info?.name || fsState.selectedVideoPath.split('/').pop();
    const duration = info?.duration_text ? ` · Длительность: ${info.duration_text}` : '';
    const size = info?.size !== undefined ? `${formatFileSize(info.size)} · ` : '';
    el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Выбрано видео</div><div class="selected-video-row">${videoThumb(fsState.selectedVideoPath, name)}<div style="min-width:0;flex:1"><div class="selection-name" title="${esc(fsState.selectedVideoPath)}">${esc(name)}</div><div class="selection-meta mono">${esc(size)}${esc(shortPath(fsState.selectedVideoPath))}${esc(duration)}</div></div>${mpvButton(fsState.selectedVideoPath)}</div></div>`;
    return;
  }
  if (manual) {
    el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Ручной путь</div><div class="selection-name" title="${esc(manual)}">${esc(shortPath(manual))}</div><div class="selection-meta mono">Advanced fallback без проверки файловым браузером</div></div>`;
    return;
  }
  el.innerHTML = '<div class="empty">Выберите видео для нарезки</div>';
}

function updateActionButtons() {
  const planBtn = document.getElementById('split-plan-btn');
  const runBtn = document.getElementById('split-btn');
  const upBtn = document.getElementById('fs-up');
  const refreshBtn = document.getElementById('fs-refresh');
  const hasPath = Boolean(getSelectedSplitPath());
  const validSeconds = Number(secsVal) > 0;
  const disabled = fsState.loading || fsState.splitting || !validSeconds || !hasPath;
  if (planBtn) {
    planBtn.disabled = disabled;
    planBtn.textContent = splitMode === 'folder' ? 'План папки' : 'План';
  }
  if (runBtn) {
    runBtn.disabled = disabled;
    runBtn.textContent = fsState.splitting
      ? 'Выполняется...'
      : splitMode === 'folder'
        ? 'Нарезать папку'
        : 'Запустить нарезку';
  }
  if (upBtn) upBtn.disabled = fsState.loading || !fsState.parentPath;
  if (refreshBtn) refreshBtn.disabled = fsState.loading || !fsState.currentPath;
}

function setSecs(value) {
  secsVal = Math.min(300, Math.max(1, value));
  document.getElementById('secs-val').textContent = secsVal + 's';
  document.getElementById('secs-range').value = secsVal;
  updateActionButtons();
}
function adjSecs(delta) { setSecs(secsVal + delta); }
function setMode(mode) {
  splitMode = mode;
  fsState.mode = mode;
  document.getElementById('mode-file').classList.toggle('on', mode === 'file');
  document.getElementById('mode-folder').classList.toggle('on', mode === 'folder');
  document.getElementById('path-lbl').textContent = mode === 'file' ? 'Ручной путь к видеофайлу' : 'Ручной путь к папке';
  document.getElementById('split-path').placeholder = mode === 'file' ? '/home/user/videos/my_video.mp4' : '/home/user/videos/';
  if (mode === 'folder' && document.getElementById('split-path').value === fsState.selectedVideoPath) {
    document.getElementById('split-path').value = '';
  }
  if (mode === 'file' && fsState.selectedVideoPath) {
    document.getElementById('split-path').value = fsState.selectedVideoPath;
  }
  document.getElementById('split-preview').innerHTML = mode === 'folder'
    ? '<div class="empty">Откройте папку и нажмите «План папки».</div>'
    : '<div class="empty">Выберите видео и нажмите «План».</div>';
  hideInlineError('split-error');
  renderSelection();
  updateActionButtons();
}
function addChip() {
  const input = document.getElementById('skip-in');
  const value = input.value.trim();
  if (!value) return;
  skipList.push(value);
  renderChips();
  input.value = '';
}
function removeChip(index) {
  skipList.splice(index, 1);
  renderChips();
}
function renderChips() {
  document.getElementById('chips').innerHTML = skipList.map((value, index) => `<span class="chip">${esc(value)}<button class="chip-del" onclick="removeChip(${index})">×</button></span>`).join('');
}
function resetSplit() {
  document.getElementById('split-message').classList.remove('on');
  document.getElementById('split-btn').disabled = false;
  document.getElementById('split-path').value = '';
  fsState.selectedVideoPath = null;
  fsState.selectedVideoInfo = null;
  skipList.length = 0;
  renderChips();
  renderSelection();
  updateActionButtons();
  document.getElementById('split-preview').innerHTML = '<div class="empty">Выберите видео или папку и нажмите «План».</div>';
  hideInlineError('split-error');
}

async function doSplit(dryRun) {
  const path = getSelectedSplitPath();
  if (!path) {
    showInlineError('split-error', splitMode === 'folder' ? 'Откройте папку для нарезки' : 'Выберите видео для нарезки');
    return;
  }
  if (Number(secsVal) <= 0) {
    showInlineError('split-error', 'Длина сегмента должна быть больше 0');
    return;
  }

  const endpoint = splitMode === 'folder'
    ? dryRun ? '/api/split-folder-dry-run' : '/api/split-folder-jobs'
    : dryRun ? '/api/split-dry-run' : '/api/split-jobs';
  const body = {
    path,
    seconds: secsVal,
    skip: skipList,
    overwrite: document.getElementById('overwrite').checked
  };

  fsState.splitting = true;
  hideInlineError('split-error');
  document.getElementById('split-message').classList.add('on');
  document.getElementById('split-msg-title').textContent = dryRun ? 'Считаю план...' : 'Выполняется...';
  document.getElementById('split-msg-sub').textContent = splitMode === 'folder' ? shortPath(path) : shortPath(path);
  updateActionButtons();

  try {
    const data = await api.post(endpoint, body);
    renderSplitResult(data);
    document.getElementById('split-msg-title').textContent = dryRun ? 'План готов' : 'Готово';
    document.getElementById('split-msg-sub').textContent = `Создано сегментов: ${data.segments_count || 0}${data.output_dir ? ' · ' + shortPath(data.output_dir) : ''}`;
    await loadDashboard();
    await loadJobs();
  } catch (err) {
    showInlineError('split-error', `Не удалось ${dryRun ? 'построить план' : 'нарезать'}:\n${err.message || err}`);
  } finally {
    fsState.splitting = false;
    updateActionButtons();
  }
}

function renderSplitResult(data) {
  const el = document.getElementById('split-preview');
  if (data.kind === 'folder' || Array.isArray(data.files)) {
    const files = data.files || [];
    if (!files.length) { el.innerHTML = '<div class="empty">Видео в папке не найдены</div>'; return; }
    el.innerHTML = `<div style="padding:10px 15px"><div class="mono mid">Папка: ${esc(shortPath(getSelectedSplitPath()))}</div><div class="mono mid">Файлов: ${data.files_count || files.length} · Успешно: ${data.ok_count || 0} · Ошибок: ${data.failed_count || 0} · Сегментов: ${data.segments_count || 0}</div></div><table class="tbl"><thead><tr><th>Файл</th><th>Статус</th><th>Сегменты</th><th>Ошибка</th><th>Действие</th></tr></thead><tbody>${files.map(file => {
      const outputDir = file.result?.output_dir || '';
      const title = outputDir
        ? `<button class="link-video mono mid ov" data-path="${esc(outputDir)}" title="Открыть папку сегментов: ${esc(outputDir)}" onclick="goToOutputFolder(this.dataset.path)">${esc(shortPath(file.path))}</button>`
        : `<span class="mono mid ov" title="${esc(file.path)}">${esc(shortPath(file.path))}</span>`;
      return `<tr><td><div class="video-name-cell">${videoThumb(file.path, file.path?.split('/').pop() || 'video')}<div style="min-width:0;flex:1">${title}${outputDir ? `<div class="mono dim ov" title="${esc(outputDir)}">Сегменты: ${esc(shortPath(outputDir))}</div>` : ''}</div></div></td><td>${badge(file.status)}</td><td class="mono txt">${esc(file.result?.segments_count || 0)}</td><td class="err ov">${esc(file.error || '')}</td><td><div class="row-actions">${mpvButton(file.path)}${outputFolderButton(outputDir)}</div></td></tr>`;
    }).join('')}</tbody></table>`;
    return;
  }
  const segments = data.segments || [];
  const shown = segments.slice(0, 100);
  const note = segments.length > shown.length ? `<div class="empty">Показано ${shown.length} из ${segments.length}</div>` : '';
  const outputLink = data.output_dir
    ? `<button class="link-video mono mid" data-path="${esc(data.output_dir)}" title="${esc(data.output_dir)}" onclick="goToOutputFolder(this.dataset.path)">${esc(shortPath(data.output_dir))}</button>`
    : '—';
  el.innerHTML = `<div style="padding:10px 15px"><div class="mono mid">Файл: ${esc(shortPath(data.source_path))}</div><div class="mono mid">Длительность: ${esc(data.duration_text || '—')} · Сегментов: ${esc(data.segments_count || segments.length)} · Вывод: ${outputLink} ${outputFolderButton(data.output_dir)}</div></div><table class="tbl"><thead><tr><th>#</th><th>Начало</th><th>Конец</th><th>Длит.</th><th>Файл</th><th>Действие</th></tr></thead><tbody>${shown.map(segment => `<tr><td class="mono dim">${segment.index}</td><td class="mono mid">${Number(segment.start_sec || 0).toFixed(2)}</td><td class="mono mid">${Number(segment.end_sec || 0).toFixed(2)}</td><td class="mono txt">${Number(segment.duration_sec || 0).toFixed(2)}</td><td><span class="mono mid ov">${esc(shortPath(segment.path || '—'))}</span></td><td>${mpvButton(segment.path)}</td></tr>`).join('')}</tbody></table>${note}`;
}

async function loadJobs() {
  try {
    const data = await api.get('/api/jobs');
    lastJobs = data.jobs || [];
    renderJobCounts(data.counts || {});
    renderJobsTable('jobs-table', lastJobs, true);
  } catch (err) {
    showError('jobs-table', err);
  }
}
function renderJobCounts(counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  for (const key of ['all','queued','running','done','failed']) {
    const el = document.getElementById('jobs-cnt-' + key);
    if (el) el.textContent = key === 'all' ? total : (counts[key] || '');
  }
}
function filterJobs(tab, status) {
  tab.closest('.tabs').querySelectorAll('.tab').forEach(item => item.classList.remove('on'));
  tab.classList.add('on');
  const rows = status === 'all' ? lastJobs : lastJobs.filter(job => job.status === status);
  renderJobsTable('jobs-table', rows, true);
}
function renderJobsTable(targetId, rows, full) {
  const el = document.getElementById(targetId);
  if (!rows.length) {
    el.innerHTML = full ? '<div class="empty">Задач пока нет. Запустите нарезку в разделе «Нарезка».</div>' : '<div class="empty">Нет задач</div>';
    return;
  }
  el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Тип</th><th>Статус</th><th>Прогресс</th><th>Файл</th>${full ? '<th>Ошибка</th><th>Старт</th>' : '<th>Создано</th>'}</tr></thead><tbody>${rows.map(job => {
    const p = percent(job.done_items, job.total_items, job.status);
    const fileCell = job.source_path
      ? `<div class="video-name-cell">${videoThumb(job.source_path, job.current_file || 'video')}<div style="min-width:0;flex:1">${job.output_dir ? `<button class="link-video mono mid ov" data-path="${esc(job.output_dir)}" title="Открыть папку сегментов: ${esc(job.output_dir)}" onclick="goToOutputFolder(this.dataset.path)">${esc(job.current_file || shortPath(job.source_path))}</button>` : `<span class="mono mid ov" title="${esc(job.source_path)}">${esc(job.current_file || shortPath(job.source_path))}</span>`}<div class="mono dim ov" title="${esc(job.output_dir || job.source_path)}">${job.output_dir ? `Сегменты: ${esc(shortPath(job.output_dir))}` : esc(shortPath(job.source_path))}</div></div><div class="row-actions">${mpvButton(job.source_path)}${outputFolderButton(job.output_dir)}</div></div>`
      : `<span class="mono mid ov">${esc(job.current_file || '—')}</span>`;
    return `<tr data-s="${esc(job.status)}"><td class="mono dim">#${job.id}</td><td class="mono mid">${esc(job.type)}</td><td>${badge(job.status, job.status === 'running')}</td><td style="min-width:100px"><div class="pbar-row"><span class="mono dim">${job.done_items||0}/${job.total_items||'?'}</span><span class="mono dim">${p}%</span></div><div class="pbar"><div class="pf ${job.status==='failed'?'pf-err':job.status==='done'?'pf-ok':'pf-info'}" style="width:${p}%"></div></div></td><td>${fileCell}</td>${full ? `<td class="err ov" style="max-width:180px">${esc(job.error || '')}</td><td class="mono dim">${esc(job.started_at || job.created_at || '')}</td>` : `<td class="mono dim">${esc(job.created_at || '')}</td>`}</tr>`;
  }).join('')}</tbody></table>`;
}

async function loadVideos() {
  try {
    const data = await api.get('/api/videos');
    lastVideos = data.videos || [];
    renderVideoCounts(data.counts || {});
    renderVideosTable(lastVideos);
  } catch (err) {
    showError('videos-table', err);
  }
}
function renderVideoCounts(counts) {
  const total = lastVideos.length;
  for (const key of ['all','inbox','reviewing','reviewed','skipped']) {
    const el = document.getElementById('vid-cnt-' + key);
    if (el) el.textContent = key === 'all' ? total : (counts[key] || '');
  }
}
function filterVideos(tab, status) {
  tab.closest('.tabs').querySelectorAll('.tab').forEach(item => item.classList.remove('on'));
  tab.classList.add('on');
  renderVideosTable(status === 'all' ? lastVideos : lastVideos.filter(video => video.review_status === status));
}
function renderVideosTable(rows) {
  const el = document.getElementById('videos-table');
  if (!rows.length) { el.innerHTML = '<div class="empty">Нет видео</div>'; return; }
  el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Название</th><th>Длительность</th><th>Статус</th><th class="r">Метки</th><th class="r">Клипы</th><th>Источник</th><th>Действие</th></tr></thead><tbody>${rows.map(video => {
    const title = video.output_dir
      ? `<button class="link-video mono txt ov" data-path="${esc(video.output_dir)}" title="Открыть папку сегментов: ${esc(video.output_dir)}" onclick="goToOutputFolder(this.dataset.path)">${esc(video.title)}</button>`
      : `<span class="mono txt ov" title="${esc(video.source_path)}">${esc(video.title)}</span>`;
    return `<tr data-s="${esc(video.review_status)}"><td class="mono dim">#${video.id}</td><td><div class="video-name-cell">${videoThumb(video.source_path, video.title)}<div style="min-width:0;flex:1">${title}${video.output_dir ? `<div class="mono dim ov" title="${esc(video.output_dir)}">Сегменты: ${esc(shortPath(video.output_dir))}</div>` : ''}</div></div></td><td class="mono mid">${esc(video.duration_text)}</td><td>${badge(video.review_status)}</td><td class="mono txt r">${video.mark_count}</td><td class="mono warn r">${video.clip_count}</td><td><span class="mono dim ov">${esc(shortPath(video.source_path))}</span></td><td><div class="row-actions">${mpvButton(video.source_path)}${outputFolderButton(video.output_dir)}</div></td></tr>`;
  }).join('')}</tbody></table>`;
}

async function loadClips() {
  try {
    const [data, accountsData] = await Promise.all([
      api.get('/api/workspace/clips'),
      api.get('/api/publish/youtube/accounts'),
    ]);
    lastClips = data.items || [];
    lastYoutubeAccounts = accountsData.accounts || lastYoutubeAccounts || [];
    syncWorkspaceYoutubeSelection();
    if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) {
      currentWorkspaceItemKey = null;
    }
    renderClipCounts(data.counts || {});
    renderWorkspaceYoutubeControls();
    renderClipsTable(getVisibleWorkspaceItems());
    renderWorkspaceDetail();
  } catch (err) {
    showError('clips-table', err);
  }
}
function renderClipCounts(counts) {
  const total = counts.all ?? lastClips.length;
  for (const key of ['all','draft','ready','queued','uploaded','failed','missing']) {
    const el = document.getElementById('clip-cnt-' + key);
    if (el) el.textContent = key === 'all' ? total : (counts[key] || '');
  }
}
function workspaceCountsFromItems(items) {
  const counts = {all: items.length, draft: 0, ready: 0, queued: 0, uploaded: 0, failed: 0, missing: 0};
  for (const item of items) {
    const status = item.workspace_status || 'draft';
    if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1;
    if (item.missing) counts.missing += 1;
  }
  return counts;
}
function filterClips(tab, status) {
  workspaceFilter = status || 'all';
  tab.closest('.tabs').querySelectorAll('.tab').forEach(item => item.classList.remove('on'));
  tab.classList.add('on');
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
}
function getVisibleWorkspaceItems() {
  if (workspaceFilter === 'missing') return lastClips.filter(item => item.missing);
  return workspaceFilter === 'all'
    ? lastClips
    : lastClips.filter(item => item.workspace_status === workspaceFilter);
}
function workspaceTypeLabel(item) {
  return item?.item_type === 'clip' ? 'Клип' : 'Сегмент';
}
function workspaceTitle(item) {
  return item?.title || item?.file_name || `${workspaceTypeLabel(item)} #${item?.item_id || ''}`;
}
function formatDurationSec(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const value = Number(seconds);
  if (!Number.isFinite(value)) return '—';
  const total = Math.max(0, Math.round(value));
  const min = Math.floor(total / 60);
  const sec = total % 60;
  return `${min}:${String(sec).padStart(2, '0')}`;
}
function workspaceItemByKey(key) {
  return lastClips.find(item => item.id === key) || null;
}
function getActiveYoutubeAccounts() {
  return (lastYoutubeAccounts || []).filter(account => (account.status || 'active') === 'active');
}
function getWorkspaceYoutubeAccount() {
  return getActiveYoutubeAccounts().find(account => Number(account.id) === Number(workspaceYoutubeState.selectedAccountId)) || null;
}
function syncWorkspaceYoutubeSelection() {
  const accounts = getActiveYoutubeAccounts();
  if (!accounts.some(account => Number(account.id) === Number(workspaceYoutubeState.selectedAccountId))) {
    workspaceYoutubeState.selectedAccountId = accounts[0] ? Number(accounts[0].id) : null;
  }
}
function onWorkspaceYoutubeAccountChange(value) {
  workspaceYoutubeState.selectedAccountId = value ? Number(value) : null;
  renderWorkspaceYoutubeControls();
}
function renderWorkspaceYoutubeControls() {
  const accountSelect = document.getElementById('workspace-youtube-account');
  const stateEl = document.getElementById('workspace-youtube-state');
  const enqueueBtn = document.getElementById('workspace-youtube-enqueue-btn');
  const uploadBtn = document.getElementById('workspace-youtube-upload-btn');
  const accounts = getActiveYoutubeAccounts();
  const hasAccount = Boolean(getWorkspaceYoutubeAccount());
  if (accountSelect) {
    if (!accounts.length) {
      accountSelect.innerHTML = '<option value="">Нет подключённых каналов</option>';
      accountSelect.disabled = true;
    } else {
      accountSelect.disabled = workspaceYoutubeState.busy;
      accountSelect.innerHTML = accounts.map(account => {
        const title = account.channel_title || account.display_name || `Канал #${account.id}`;
        const email = account.account_email ? ` · ${account.account_email}` : '';
        return `<option value="${Number(account.id)}"${Number(account.id) === Number(workspaceYoutubeState.selectedAccountId) ? ' selected' : ''}>${esc(title)}${esc(email)}</option>`;
      }).join('');
    }
  }
  const disabled = workspaceYoutubeState.busy || !hasAccount;
  if (enqueueBtn) enqueueBtn.disabled = disabled;
  if (uploadBtn) uploadBtn.disabled = disabled;
  if (stateEl) {
    stateEl.textContent = hasAccount
      ? 'В очередь добавляются только элементы со статусом «Готово».'
      : 'Сначала подключите YouTube-канал в настройках публикации.';
  }
  updateWorkspaceDetailActionState();
}
function renderWorkspaceType(item) {
  const cls = item.item_type === 'clip' ? 'workspace-type clip' : 'workspace-type segment';
  return `<span class="${cls}">${esc(workspaceTypeLabel(item))}</span>`;
}
function missingBadge(item) {
  return item?.missing ? '<span class="badge b-err">Файл отсутствует</span>' : '';
}
function workspaceOpenFileButton(item, label='Открыть') {
  const path = item?.path || item?.source_path || '';
  if (!path || item?.missing || !item?.file_exists) {
    return `<button class="btn-mini" disabled title="${esc(item?.path_error || 'Файл отсутствует')}">${esc(label)}</button>`;
  }
  return `<button class="btn-mini" data-path="${esc(path)}" onclick="openVideoInMpv(this.dataset.path)">${esc(label)}</button>`;
}
function workspaceOpenFolderButton(item, label='Папка') {
  if (!item?.folder_path || !item?.folder_exists) {
    return `<button class="btn-mini" disabled title="${esc(item?.path_error || 'Папка отсутствует')}">${esc(label)}</button>`;
  }
  return `<button class="btn-mini" data-path="${esc(item.folder_path)}" onclick="goToOutputFolder(this.dataset.path)">${esc(label)}</button>`;
}
function toggleWorkspaceSelection(key, checked) {
  if (checked) selectedWorkspaceKeys.add(key);
  else selectedWorkspaceKeys.delete(key);
  renderWorkspaceBulkState();
}
function selectWorkspaceItem(key) {
  currentWorkspaceItemKey = key;
  if (key) selectedWorkspaceKeys.add(key);
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
}
function renderWorkspaceBulkState() {
  const total = selectedWorkspaceKeys.size;
  document.querySelectorAll('[data-workspace-selected-count]').forEach(el => {
    el.textContent = total ? `Выбрано: ${total}` : '';
  });
}
function renderWorkspaceListAndDetail() {
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
}
function renderClipsTable(rows) {
  const el = document.getElementById('clips-table');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">Нарезанных сегментов и клипов пока нет. После нарезки видео файлы появятся здесь.</div>';
    return;
  }
  el.innerHTML = `<div class="workspace-selected-line mono dim" data-workspace-selected-count></div><table class="tbl workspace-table"><thead><tr><th></th><th>Файл</th><th>Источник</th><th>Длит.</th><th>Тип</th><th>Статус</th><th>Путь</th><th>Действие</th></tr></thead><tbody>${rows.map(item => {
    const selected = selectedWorkspaceKeys.has(item.id);
    const activeClasses = ['workspace-row'];
    if (currentWorkspaceItemKey === item.id) activeClasses.push('active');
    if (item.missing) activeClasses.push('missing');
    const active = ` class="${activeClasses.join(' ')}"`;
    const playablePath = item.path || item.source_path;
    const title = workspaceTitle(item);
    const renderInfo = item.render_status ? `<div class="mono dim">render: ${esc(ruStatus(item.render_status))}</div>` : '';
    const publishInfo = item.publish_job_status ? `<div class="mono dim">publish #${esc(item.publish_job_id || '')}: ${esc(ruStatus(item.publish_job_status))}</div>` : '';
    return `<tr${active} data-key="${esc(item.id)}" onclick="selectWorkspaceItem('${esc(item.id)}')"><td><input type="checkbox" ${selected ? 'checked' : ''} onclick="event.stopPropagation();toggleWorkspaceSelection('${esc(item.id)}', this.checked)"></td><td><div class="video-name-cell">${videoThumb(playablePath, title)}<div style="min-width:0;flex:1"><div class="mono txt ov" title="${esc(title)}">${esc(title)}</div><div class="mono dim">#${esc(item.item_id)} · ${esc(item.file_name || '—')}</div>${renderInfo}${publishInfo}</div></div></td><td class="mono mid ov">${esc(item.video_title || '—')}</td><td class="mono txt">${esc(formatDurationSec(item.duration_sec))}</td><td>${renderWorkspaceType(item)}</td><td><div class="status-stack">${badge(item.workspace_status)}${missingBadge(item)}</div></td><td><span class="mono dim ov" title="${esc(item.path || '')}">${esc(shortPath(item.path || '—'))}</span></td><td><div class="row-actions">${workspaceOpenFileButton(item)}${workspaceOpenFolderButton(item)}${item.missing ? `<button class="btn-mini" onclick="event.stopPropagation();deleteWorkspaceItem('${esc(item.id)}')">Убрать</button>` : ''}</div></td></tr>`;
  }).join('')}</tbody></table>`;
  renderWorkspaceBulkState();
}
function selectAllWorkspaceItems() {
  const rows = getVisibleWorkspaceItems();
  const allSelected = rows.length && rows.every(item => selectedWorkspaceKeys.has(item.id));
  if (allSelected) rows.forEach(item => selectedWorkspaceKeys.delete(item.id));
  else rows.forEach(item => selectedWorkspaceKeys.add(item.id));
  renderClipsTable(rows);
}
function clearWorkspaceSelection() {
  selectedWorkspaceKeys.clear();
  renderClipsTable(getVisibleWorkspaceItems());
}
async function refreshWorkspaceList() {
  const data = await api.get('/api/workspace/clips');
  lastClips = data.items || [];
  if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) {
    currentWorkspaceItemKey = null;
  }
  renderClipCounts(data.counts || workspaceCountsFromItems(lastClips));
  return lastClips;
}
function workspaceYoutubeRequestBody(items) {
  return {
    item_keys: items,
    account_id: Number(workspaceYoutubeState.selectedAccountId),
    publish_mode: document.getElementById('workspace-youtube-mode')?.value || 'private',
    category_id: document.getElementById('workspace-youtube-category')?.value || '22',
    made_for_kids: Boolean(document.getElementById('workspace-youtube-made-for-kids')?.checked),
  };
}
function workspaceYoutubeSummary(data) {
  const created = data?.created || 0;
  const updated = data?.updated || 0;
  const skipped = data?.skipped || 0;
  const errors = data?.errors || 0;
  return `Добавлено в очередь: ${created} · обновлено: ${updated} · пропущено: ${skipped} · ошибок: ${errors}`;
}
function workspaceYoutubeSkippedText(data) {
  const skipped = data?.skipped_items || [];
  if (!skipped.length) return '';
  return skipped.slice(0, 12).map(item => `${item.item_key}: ${item.reason}`).join('\n');
}
function confirmYoutubeBatch(count, mode, actionText) {
  const visibility = mode || 'private';
  if (count > 5) {
    const ok = confirm(`Вы собираетесь ${actionText || 'отправить'} ${count} видео в YouTube. Видимость: ${visibility}. Продолжить?`);
    if (!ok) return false;
  }
  if (visibility === 'public') {
    return confirm('Видео будут опубликованы публично. Это действие может быть видно зрителям. Продолжить?');
  }
  return true;
}
function publishVisibilitySummary(jobs, fallback = 'private') {
  const values = Array.from(new Set((jobs || []).map(job => job.privacy_status || job.publish_mode || fallback).filter(Boolean)));
  if (!values.length) return fallback;
  return values.length === 1 ? values[0] : values.join('/');
}
function confirmPublishJobsBatch(jobs, count, actionText) {
  const selectedJobs = jobs || [];
  const effectiveCount = Number(count || selectedJobs.length || 0);
  const visibility = publishVisibilitySummary(selectedJobs, 'private');
  if (!confirmYoutubeBatch(effectiveCount, visibility, actionText)) return false;
  if (selectedJobs.some(job => (job.privacy_status || job.publish_mode) === 'public') && visibility !== 'public') {
    return confirm('Среди выбранных видео есть публичные публикации. Это действие может быть видно зрителям. Продолжить?');
  }
  return true;
}
async function applyWorkspaceYoutubeResponse(data) {
  const workspace = data?.workspace || {};
  lastClips = workspace.items || lastClips;
  selectedWorkspaceKeys = new Set(Array.from(selectedWorkspaceKeys).filter(key => workspaceItemByKey(key)));
  if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) currentWorkspaceItemKey = null;
  renderClipCounts(workspace.counts || workspaceCountsFromItems(lastClips));
  renderWorkspaceListAndDetail();
  await refreshPublishJobs();
}
async function enqueueWorkspaceItemsToYouTube(items, runNow = false) {
  let selected = (items || []).filter(Boolean);
  if (!selected.length) {
    showToast('Сначала выберите готовые элементы', 'err');
    return;
  }
  if (!getWorkspaceYoutubeAccount()) {
    showToast('Сначала подключите YouTube-канал в настройках публикации.', 'err');
    return;
  }
  const mode = document.getElementById('workspace-youtube-mode')?.value || 'private';
  if (!confirmYoutubeBatch(selected.length, mode, runNow ? 'загрузить' : 'отправить')) return;
  if (runNow && !confirm('Добавить выбранные видео в очередь и сразу запустить загрузку?')) return;
  workspaceYoutubeState.busy = true;
  renderWorkspaceYoutubeControls();
  try {
    if (currentWorkspaceItemKey && selected.includes(currentWorkspaceItemKey)) {
      const updated = await saveWorkspaceDetail({silent: true, rerender: false});
      if (updated) {
        selected = selected.map(key => key === currentWorkspaceItemKey ? updated.id : key);
      }
    }
    const data = await api.post('/api/workspace/clips/youtube/enqueue', workspaceYoutubeRequestBody(selected));
    await applyWorkspaceYoutubeResponse(data);
    showToast(workspaceYoutubeSummary(data));
    const skippedText = workspaceYoutubeSkippedText(data);
    if (skippedText) alert(`Пропущенные элементы:\n${skippedText}`);
    if (runNow && (data.created || 0) > 0) {
      const worker = await api.post('/api/publish/worker/run-once', {limit: Math.max(1, data.created || 1)});
      showToast(`Запущена загрузка. Обработано jobs: ${worker.processed || 0}`);
      await refreshWorkspaceList();
      renderWorkspaceListAndDetail();
      await refreshPublishJobs();
    }
  } catch (err) {
    showToast(err.message || 'Не удалось добавить в очередь YouTube', 'err');
  } finally {
    workspaceYoutubeState.busy = false;
    renderWorkspaceYoutubeControls();
  }
}
async function enqueueSelectedWorkspaceToYouTube(runNow = false) {
  await enqueueWorkspaceItemsToYouTube(Array.from(selectedWorkspaceKeys), runNow);
}
async function enqueueCurrentWorkspaceToYouTube(runNow = false) {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item) return;
  await enqueueWorkspaceItemsToYouTube([item.id], runNow);
}
async function selectMissingWorkspaceItems() {
  try {
    await refreshWorkspaceList();
    const missingItems = lastClips.filter(item => item.missing);
    if (!missingItems.length) {
      showToast('Отсутствующих файлов не найдено.');
      renderWorkspaceListAndDetail();
      return;
    }
    selectedWorkspaceKeys = new Set(missingItems.map(item => item.id));
    renderWorkspaceListAndDetail();
    showToast(`Выбрано отсутствующих: ${missingItems.length}`);
  } catch (err) {
    showToast(err.message || 'Не удалось выбрать отсутствующие', 'err');
  }
}
async function bulkSetWorkspaceStatus(status) {
  const items = Array.from(selectedWorkspaceKeys);
  if (!items.length) {
    showToast('Сначала выберите сегменты или клипы', 'err');
    return;
  }
  try {
    const data = await api.post('/api/workspace/clips/bulk-status', {items, workspace_status: status});
    lastClips = data.items || [];
    renderClipCounts(data.counts || {});
    renderWorkspaceListAndDetail();
    showToast(`Обновлено: ${data.updated || 0}`);
  } catch (err) {
    showToast(err.message || 'Не удалось обновить статус', 'err');
  }
}
function openSelectedWorkspaceFolder() {
  const key = Array.from(selectedWorkspaceKeys)[0] || currentWorkspaceItemKey;
  const item = workspaceItemByKey(key);
  if (!item?.folder_path || !item?.folder_exists) {
    showToast(item?.path_error || 'Папка отсутствует', 'err');
    return;
  }
  goToOutputFolder(item.folder_path);
}
function workspaceDeleteSummary(summary) {
  if (!summary) return 'Готово';
  return `Удалено файлов: ${summary.deleted_files || 0} · уже отсутствовали: ${summary.already_missing || 0} · ошибок: ${summary.errors || 0}`;
}
async function refreshWorkspaceFromDeleteResponse(data) {
  lastClips = data.items || [];
  selectedWorkspaceKeys = new Set(Array.from(selectedWorkspaceKeys).filter(key => workspaceItemByKey(key)));
  if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) currentWorkspaceItemKey = null;
  renderClipCounts(data.counts || workspaceCountsFromItems(lastClips));
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
}
async function deleteWorkspaceItem(key) {
  const item = workspaceItemByKey(key || currentWorkspaceItemKey);
  if (!item) return;
  if (item.file_exists) {
    const ok = confirm('Удалить файл с диска? Это действие нельзя отменить.');
    if (!ok) return;
  }
  try {
    const data = await api.del(`/api/workspace/clips/${encodeURIComponent(item.id)}`);
    await refreshWorkspaceFromDeleteResponse(data);
    showToast(item.file_exists ? 'Файл удалён' : 'Запись убрана из списка');
  } catch (err) {
    showToast(err.message || 'Не удалось удалить элемент', 'err');
  }
}
async function bulkDeleteWorkspaceItems(items = null) {
  const selected = items || Array.from(selectedWorkspaceKeys);
  if (!selected.length) {
    showToast('Сначала выберите сегменты или клипы', 'err');
    return;
  }
  const hasExistingFiles = selected
    .map(key => workspaceItemByKey(key))
    .some(item => item?.file_exists);
  if (hasExistingFiles && !confirm('Удалить выбранные файлы с диска? Это действие нельзя отменить.')) return;
  try {
    const data = await api.post('/api/workspace/clips/bulk-delete', {items: selected});
    await refreshWorkspaceFromDeleteResponse(data);
    showToast(workspaceDeleteSummary(data.summary));
  } catch (err) {
    showToast(err.message || 'Не удалось удалить выбранные элементы', 'err');
  }
}
async function cleanupMissingWorkspaceItems() {
  try {
    const data = await api.post('/api/workspace/clips/cleanup-missing', {});
    await refreshWorkspaceFromDeleteResponse(data);
    const hidden = data.summary?.hidden || 0;
    showToast(`Очищено отсутствующих: ${hidden}`);
  } catch (err) {
    showToast(err.message || 'Не удалось очистить отсутствующие', 'err');
  }
}
async function cleanupSelectedMissingWorkspaceItems() {
  const missing = Array.from(selectedWorkspaceKeys).filter(key => workspaceItemByKey(key)?.missing);
  if (!missing.length) {
    showToast('Среди выбранных нет отсутствующих файлов', 'err');
    return;
  }
  await bulkDeleteWorkspaceItems(missing);
}
function futureFeature(name) {
  // Future hook: connect YouTube queue, subtitle generation, and uniqueness filters here.
  showToast(`${name || 'Функция'} будет добавлена позже`);
}
function renderWorkspaceDetail() {
  const el = document.getElementById('workspace-detail');
  if (!el) return;
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item) {
    el.innerHTML = '<div class="empty">Выберите сегмент или клип</div>';
    return;
  }
  const playablePath = item.path || item.source_path;
  const title = workspaceTitle(item);
  const missingNotice = item.missing
    ? `<div class="missing-panel">${badge('failed')}<div><b>Файл был удалён или перенесён.</b><p>${esc(item.path_error || 'Можно убрать запись из рабочего пространства.')}</p></div></div>`
    : '';
  const fileAction = item.missing
    ? `<button class="btn-danger" onclick="deleteWorkspaceItem('${esc(item.id)}')">Убрать из списка</button>`
    : `<button class="btn-danger" onclick="deleteWorkspaceItem('${esc(item.id)}')">Удалить файл</button>`;
  const readyDisabled = item.workspace_status === 'ready' ? ' disabled' : '';
  const draftDisabled = item.workspace_status === 'draft' ? ' disabled' : '';
  const youtubeDisabled = (!getWorkspaceYoutubeAccount() || item.missing || item.workspace_status !== 'ready') ? ' disabled' : '';
  const publishPanel = item.publish_job_id
    ? `<div class="missing-panel publish-panel">${badge(item.publish_job_status || 'queued')}<div><b>Publish job #${esc(item.publish_job_id)}</b><p>${item.publish_youtube_url ? `<a class="link-video mono txt" href="${esc(item.publish_youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>` : 'YouTube URL пока нет.'}${item.publish_error ? `<br><span class="err">${esc(shortErrorText(item.publish_error))}</span>` : ''}</p></div></div>`
    : '';
  el.innerHTML = `<div class="workspace-detail-body">
    <div class="workspace-preview">${videoThumb(playablePath, title)}</div>
    <div class="workspace-detail-head">
      <div>
        <div class="workspace-detail-title">${esc(title)}</div>
        <div class="mono dim detail-badges">${renderWorkspaceType(item)} · #${esc(item.item_id)} · ${badge(item.workspace_status)} ${missingBadge(item)}</div>
      </div>
    </div>
    ${missingNotice}
    ${publishPanel}
    <div class="workspace-meta">
      <div><span>Источник</span><b>${esc(item.video_title || '—')}</b></div>
      <div><span>Длительность</span><b>${esc(formatDurationSec(item.duration_sec))}</b></div>
      <div><span>Файл</span><b title="${esc(item.path || '')}">${esc(shortPath(item.path || '—'))}</b></div>
      <div><span>Папка</span><b title="${esc(item.folder_path || '')}">${esc(shortPath(item.folder_path || '—'))}</b></div>
    </div>
    <div class="field"><label class="field-lbl">Статус</label><select id="workspace-status" onchange="updateWorkspaceDetailActionState()"><option value="draft">Черновик</option><option value="ready">Готово</option><option value="queued">В очереди</option><option value="uploaded">Загружено</option><option value="failed">Ошибка</option></select></div>
    <div class="field"><label class="field-lbl">Название</label><input id="workspace-title" type="text" value="${esc(item.title || '')}" placeholder="${esc(item.file_name || title)}"></div>
    <div class="field"><label class="field-lbl">Описание</label><textarea id="workspace-description" rows="5" placeholder="Локальное описание для будущей публикации">${esc(item.description || '')}</textarea></div>
    <div class="field"><label class="field-lbl">Теги</label><input id="workspace-tags" type="text" value="${esc(item.tags || '')}" placeholder="через запятую"></div>
    <div class="workspace-detail-actions">
      <button class="btn-primary" onclick="saveWorkspaceDetail()">Сохранить</button>
      ${workspaceOpenFileButton(item, 'Открыть файл')}
      ${workspaceOpenFolderButton(item, 'Открыть папку')}
      <button class="btn-secondary" onclick="setCurrentWorkspaceStatus('ready')"${readyDisabled}>Сделать готовым</button>
      <button class="btn-secondary" onclick="setCurrentWorkspaceStatus('draft')"${draftDisabled}>Вернуть в черновики</button>
      ${fileAction}
      <button class="btn-secondary" id="workspace-detail-enqueue-youtube" onclick="enqueueCurrentWorkspaceToYouTube(false)"${youtubeDisabled}>Добавить в очередь YouTube</button>
      <button class="btn-primary" id="workspace-detail-upload-youtube" onclick="enqueueCurrentWorkspaceToYouTube(true)"${youtubeDisabled}>Загрузить сейчас</button>
      <button class="btn-secondary stub-action" onclick="futureFeature('Субтитры')">Добавить субтитры</button>
      <button class="btn-secondary stub-action" onclick="futureFeature('Уникализация')">Уникализировать</button>
    </div>
  </div>`;
  const statusEl = document.getElementById('workspace-status');
  if (statusEl) statusEl.value = item.workspace_status || 'draft';
  updateWorkspaceDetailActionState();
}
function currentWorkspaceFormPayload(item) {
  return {
    workspace_status: document.getElementById('workspace-status')?.value || item.workspace_status,
    title: document.getElementById('workspace-title')?.value || '',
    description: document.getElementById('workspace-description')?.value || '',
    tags: document.getElementById('workspace-tags')?.value || '',
  };
}
function updateWorkspaceDetailActionState() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  const status = document.getElementById('workspace-status')?.value || item?.workspace_status || 'draft';
  const canPublish = Boolean(getWorkspaceYoutubeAccount()) && Boolean(item) && !item.missing && status === 'ready';
  ['workspace-detail-enqueue-youtube', 'workspace-detail-upload-youtube'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = !canPublish;
  });
}
async function saveWorkspaceDetail(options = {}) {
  const {silent = false, rerender = true} = options;
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item) return null;
  try {
    const data = await api.patch(`/api/workspace/clips/${encodeURIComponent(item.id)}`, currentWorkspaceFormPayload(item));
    const updated = data.item;
    lastClips = lastClips.map(row => row.id === updated.id ? updated : row);
    currentWorkspaceItemKey = updated.id;
    if (rerender) {
      renderClipCounts(workspaceCountsFromItems(lastClips));
      renderClipsTable(getVisibleWorkspaceItems());
      renderWorkspaceDetail();
    }
    if (!silent) showToast('Сохранено');
    return updated;
  } catch (err) {
    if (!silent) showToast(err.message || 'Не удалось сохранить', 'err');
    throw err;
  }
}
async function setCurrentWorkspaceStatus(status) {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item) return;
  const statusEl = document.getElementById('workspace-status');
  if (statusEl) statusEl.value = status;
  await saveWorkspaceDetail();
}
async function renderQueued() {
  try {
    const data = await api.post('/api/render', {limit: 10});
    showToast(`Готово. Отрендерено: ${data.count}`);
    await loadClips();
    await loadDashboard();
  } catch (err) {
    showToast(err.message || 'Не удалось запустить рендер', 'err');
  }
}
async function retryFailedClips() {
  try {
    const data = await api.post('/api/retry-failed', {});
    showToast(`Сброшено в очередь: ${data.reset_count}`);
    await loadClips();
    await loadDashboard();
  } catch (err) {
    showToast(err.message || 'Не удалось повторить ошибки', 'err');
  }
}

function setPublishTab(id, btn) {
  currentPublishTab = id;
  document.querySelectorAll('[data-pub-tab]').forEach(item => item.classList.remove('on'));
  if (btn) btn.classList.add('on');
  document.querySelectorAll('.pub-tab').forEach(item => item.classList.remove('on'));
  const panel = document.getElementById('pub-' + id);
  if (panel) panel.classList.add('on');
  if (id === 'youtube' || id === 'accounts') loadPublishView();
}

function renderPublishError(message) {
  if (!message) {
    hideInlineError('publish-error');
    return;
  }
  showInlineError('publish-error', message || 'Неизвестная ошибка');
}

function getSelectedAccount() {
  return (lastYoutubeAccounts || []).find(account => Number(account.id) === Number(publishState.selectedAccountId)) || null;
}

function getSelectedPublishClip() {
  return (lastReadyPublishClips || []).find(clip => Number(clip.id) === Number(publishState.selectedClipId)) || null;
}

function getActiveOAuthProfiles() {
  return (lastYoutubeProfiles || []).filter(profile => (profile.status || 'active') === 'active');
}

function isEnvOAuthProfile(profile) {
  return profile?.mode === 'env';
}

function oauthProfileSourceLabel(profile) {
  if (!profile) return '—';
  if (isEnvOAuthProfile(profile)) return 'окружение';
  if (profile.mode === 'legacy') return 'legacy settings';
  return 'ручной профиль';
}

function getSelectedProfile() {
  return getActiveOAuthProfiles().find(profile => Number(profile.id) === Number(publishState.selectedProfileId)) || null;
}

function getVisibleYoutubeAccounts() {
  const selectedProfile = getSelectedProfile();
  if (!selectedProfile) return lastYoutubeAccounts || [];
  return (lastYoutubeAccounts || []).filter(account => {
    if (!account.oauth_profile_id) return true;
    return Number(account.oauth_profile_id) === Number(selectedProfile.id);
  });
}

function syncPublishSelections() {
  const profiles = getActiveOAuthProfiles();
  if (!profiles.some(profile => Number(profile.id) === Number(publishState.selectedProfileId))) {
    const defaultProfile = profiles.find(profile => profile.is_default) || profiles[0];
    publishState.selectedProfileId = defaultProfile ? Number(defaultProfile.id) : null;
  }

  const accounts = getVisibleYoutubeAccounts();
  if (!accounts.some(account => Number(account.id) === Number(publishState.selectedAccountId))) {
    publishState.selectedAccountId = accounts[0] ? Number(accounts[0].id) : null;
  }

  if (!(lastReadyPublishClips || []).some(clip => Number(clip.id) === Number(publishState.selectedClipId))) {
    publishState.selectedClipId = lastReadyPublishClips[0] ? Number(lastReadyPublishClips[0].id) : null;
  }

  if (lastYoutubeAccounts.length) publishState.onboardingHint = '';
}

function renderPublishConnectButton() {
  const html = '<i class="ti ti-brand-youtube" style="font-size:12px;vertical-align:-1px"></i> Подключить канал';
  ['publish-connect-btn', 'publish-connect-btn-accounts'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = publishState.busy;
    btn.innerHTML = html;
  });
}

function renderPublishStatePanel() {
  const el = document.getElementById('publish-state');
  if (!el) return;
  const profiles = getActiveOAuthProfiles();
  const selectedProfile = getSelectedProfile();

  if (!profiles.length) {
    el.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('error')}<span class="mono txt">OAuth client не настроен</span></div><p>Создайте OAuth client в Google Cloud, затем импортируйте JSON в настройках. После этого можно подключить YouTube-канал.</p><div class="action-row"><button class="btn-secondary" onclick="openYouTubeSettings()">Открыть настройки YouTube OAuth</button></div></div>`;
    return;
  }

  if (!lastYoutubeAccounts.length) {
    const source = selectedProfile ? ` · ${oauthProfileSourceLabel(selectedProfile)}` : '';
    const hint = publishState.onboardingHint ? `<p class="err">${esc(publishState.onboardingHint)}</p>` : '';
    el.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('active')}<span class="mono txt">OAuth client готов${esc(source)}</span></div><p>Выберите OAuth client и нажмите «Подключить канал», чтобы открыть Google Consent Screen.</p>${hint}</div>`;
    return;
  }

  el.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('active')}<span class="mono txt">Каналы подключены</span></div><p>Выберите канал и готовый клип, затем добавьте публикацию в очередь или загрузите ролик сразу.</p></div>`;
}

function renderPublishAccountsPanel() {
  const stateEl = document.getElementById('publish-accounts-state');
  const listEl = document.getElementById('publish-accounts-list');
  if (!stateEl || !listEl) return;
  const profiles = getActiveOAuthProfiles();

  if (!lastYoutubeAccounts.length) {
    const text = profiles.length
      ? 'Нажмите «Подключить канал», чтобы добавить YouTube-канал через Google OAuth.'
      : 'Сначала добавьте OAuth client в настройках YouTube OAuth.';
    stateEl.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge(profiles.length ? 'active' : 'error')}<span class="mono txt">YouTube-каналы ещё не подключены</span></div><p>${esc(text)}</p></div>`;
    listEl.innerHTML = '<div class="empty">Подключённых YouTube-каналов пока нет.</div>';
    return;
  }

  stateEl.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('active')}<span class="mono txt">Подключённые каналы</span></div><p>Здесь можно проверить подключённые каналы и отключить лишние.</p></div>`;
  listEl.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Аккаунт</th><th>Канал</th><th>Google OAuth client</th><th>Статус</th><th>Подключён</th><th>Обновлён</th><th>Действие</th></tr></thead><tbody>${lastYoutubeAccounts.map(account => {
    const displayName = account.display_name || account.channel_title || 'YouTube аккаунт';
    const channel = account.channel_title || account.channel_id || '—';
    const profile = account.profile_name || (account.oauth_profile_id ? `Profile #${account.oauth_profile_id}` : 'default');
    const error = account.error ? `<div class="mono err">${esc(account.error)}</div>` : '';
    const disabled = account.status === 'disconnected';
    return `<tr><td class="mono dim">#${account.id}</td><td><div class="mono txt">${esc(displayName)}</div>${account.account_email ? `<div class="mono dim">${esc(account.account_email)}</div>` : ''}${error}</td><td><div class="mono mid">${esc(channel)}</div>${account.channel_id ? `<div class="mono dim">${esc(account.channel_id)}</div>` : ''}</td><td class="mono dim">${esc(profile)}</td><td>${badge(account.status)}</td><td class="mono dim">${esc(formatMtime(account.created_at))}</td><td class="mono dim">${esc(formatMtime(account.updated_at))}</td><td><button class="btn-danger" ${disabled ? 'disabled' : ''} onclick="disconnectYouTubeAccount(${Number(account.id)})">Отключить</button></td></tr>`;
  }).join('')}</tbody></table>`;
}

function renderPublishProfileSelect() {
  const select = document.getElementById('publish-profile-select');
  const meta = document.getElementById('publish-profile-meta');
  if (!select) return;
  const profiles = getActiveOAuthProfiles();
  if (!profiles.length) {
    select.innerHTML = '<option value="">OAuth client не найден</option>';
    select.disabled = true;
    if (meta) meta.innerHTML = '<div>Создайте OAuth client в Google Cloud и импортируйте JSON в настройках.</div>';
    return;
  }
  select.disabled = false;
  select.innerHTML = profiles.map(profile => {
    const suffix = [
      profile.is_default ? 'default' : '',
      oauthProfileSourceLabel(profile),
    ].filter(Boolean).join(' · ');
    return `<option value="${Number(profile.id)}"${Number(profile.id) === Number(publishState.selectedProfileId) ? ' selected' : ''}>${esc(profile.name || `Profile #${profile.id}`)}${suffix ? ` · ${esc(suffix)}` : ''}</option>`;
  }).join('');
  const selected = getSelectedProfile();
  if (meta && selected) {
    const secret = selected.client_secret_set ? 'secret сохранён' : 'secret не задан';
    const redirect = selected.redirect_uri ? `<div>Redirect URI: <span class="mono">${esc(selected.redirect_uri)}</span></div>` : '';
    meta.innerHTML = `<div>${esc(oauthProfileSourceLabel(selected))} · ${esc(secret)}</div>${redirect}`;
  }
}

function renderPublishAccountSelect() {
  const select = document.getElementById('publish-account-select');
  const meta = document.getElementById('publish-account-meta');
  if (!select) return;
  const accounts = getVisibleYoutubeAccounts();
  if (!accounts.length) {
    select.innerHTML = '<option value="">Нет подключённых каналов</option>';
    select.disabled = true;
    if (meta) meta.innerHTML = '<div>Подключите канал через выбранный OAuth client.</div>';
    return;
  }
  select.disabled = false;
  select.innerHTML = accounts.map(account => {
    const title = account.channel_title || account.display_name || `Канал #${account.id}`;
    const profileName = account.profile_name ? ` · ${account.profile_name}` : '';
    return `<option value="${Number(account.id)}"${Number(account.id) === Number(publishState.selectedAccountId) ? ' selected' : ''}>${esc(title)}${esc(profileName)}</option>`;
  }).join('');
  const selectedAccount = getSelectedAccount();
  if (meta && selectedAccount) {
    const email = selectedAccount.account_email ? `<div>Email: ${esc(selectedAccount.account_email)}</div>` : '';
    const error = selectedAccount.error ? `<div class="err">Ошибка: ${esc(selectedAccount.error)}</div>` : '';
    meta.innerHTML = `<div>Статус: ${esc(ruStatus(selectedAccount.status))}</div>${email}${error}`;
  }
}

function renderReadyPublishClips() {
  const el = document.getElementById('publish-ready-clips');
  if (!el) return;
  if (!lastReadyPublishClips.length) {
    el.innerHTML = '<div class="empty">Нет готовых клипов. Сначала завершите рендер в разделе «Клипы».</div>';
    return;
  }
  el.innerHTML = `<table class="tbl compact"><thead><tr><th>#</th><th>Клип</th><th>Видео</th><th>Файл</th><th>Действие</th></tr></thead><tbody>${lastReadyPublishClips.map(clip => {
    const selected = Number(clip.id) === Number(publishState.selectedClipId);
    const playable = clip.output_path || clip.source_path;
    return `<tr><td class="mono dim">#${clip.id}</td><td><div class="video-name-cell">${videoThumb(playable, clip.video_title || 'clip')}<div style="min-width:0;flex:1"><div class="mono txt ov">${esc(clip.video_title || `Клип #${clip.id}`)}</div><div class="mono dim ov">${esc(shortPath(clip.output_path || playable || '—'))}</div></div></div></td><td class="mono mid">${esc(clip.video_title || '—')}</td><td class="mono dim ov">${esc(shortPath(clip.output_path || '—'))}</td><td><div class="row-actions"><button class="btn-mini${selected ? ' on' : ''}" onclick="selectPublishClip(${Number(clip.id)})">Выбрать</button>${mpvButton(playable)}</div></td></tr>`;
  }).join('')}</tbody></table>`;
}

function renderSelectedPublishClip() {
  const el = document.getElementById('publish-selected-clip');
  if (!el) return;
  const clip = getSelectedPublishClip();
  if (!clip) {
    el.innerHTML = '<div class="empty">Выберите готовый клип для публикации</div>';
    return;
  }
  const playable = clip.output_path || clip.source_path;
  el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Выбран клип</div><div class="selected-video-row">${videoThumb(playable, clip.video_title || 'clip')}<div style="min-width:0;flex:1"><div class="selection-name">${esc(clip.video_title || `Клип #${clip.id}`)}</div><div class="selection-meta mono">${esc(shortPath(clip.output_path || playable || '—'))}</div></div><div class="row-actions">${mpvButton(playable)}</div></div></div>`;
}

function publishJobCountsFromItems(items) {
  const counts = {all: items.length, queued: 0, uploading: 0, done: 0, failed: 0, cancelled: 0};
  for (const job of items) {
    const status = job.status || 'queued';
    if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1;
  }
  return counts;
}
function renderPublishJobCounts() {
  const counts = publishJobCountsFromItems(lastPublishJobs.filter(job => !hiddenDonePublishJobIds.has(Number(job.id))));
  for (const key of ['all','queued','uploading','done','failed','cancelled']) {
    const el = document.getElementById('pubjob-cnt-' + key);
    if (el) el.textContent = key === 'all' ? counts.all : (counts[key] || '');
  }
}
function getVisiblePublishJobs() {
  return lastPublishJobs.filter(job => {
    if (hiddenDonePublishJobIds.has(Number(job.id))) return false;
    if (publishJobFilter === 'all') return true;
    return job.status === publishJobFilter;
  });
}
function filterPublishJobs(tab, status) {
  publishJobFilter = status || 'all';
  document.querySelectorAll('[data-publish-filter]').forEach(item => item.classList.remove('on'));
  if (tab) tab.classList.add('on');
  renderPublishJobsTable();
}
function togglePublishJobSelection(jobId, checked) {
  const id = Number(jobId);
  if (checked) selectedPublishJobIds.add(id);
  else selectedPublishJobIds.delete(id);
  renderPublishJobSelectionState();
}
function renderPublishJobSelectionState() {
  document.querySelectorAll('[data-publish-selected-count]').forEach(el => {
    el.textContent = selectedPublishJobIds.size ? `Выбрано jobs: ${selectedPublishJobIds.size}` : '';
  });
}
function shortErrorText(value) {
  const text = String(value || '');
  return text.length > 90 ? text.slice(0, 87) + '...' : text;
}
function showPublishJobError(jobId) {
  const job = lastPublishJobs.find(item => Number(item.id) === Number(jobId));
  if (!job?.error) return;
  alert(job.error);
}
async function copyPublishJobError(jobId) {
  const job = lastPublishJobs.find(item => Number(item.id) === Number(jobId));
  if (!job?.error) return;
  try {
    await navigator.clipboard.writeText(job.error);
    showToast('Ошибка скопирована');
  } catch {
    alert(job.error);
  }
}
function renderPublishJobsTable() {
  const el = document.getElementById('publish-jobs');
  if (!el) return;
  renderPublishJobCounts();
  const rows = getVisiblePublishJobs();
  selectedPublishJobIds = new Set(Array.from(selectedPublishJobIds).filter(id => lastPublishJobs.some(job => Number(job.id) === Number(id))));
  if (!rows.length) {
    el.innerHTML = '<div class="empty">Публикаций пока нет. Выберите канал и добавьте клип в очередь.</div>';
    return;
  }
  el.innerHTML = `<div class="workspace-selected-line mono dim" data-publish-selected-count></div><table class="tbl publish-jobs-table"><thead><tr><th></th><th>Job</th><th>Status</th><th>Title</th><th>Channel</th><th>Privacy</th><th>Mode</th><th>File</th><th>Created</th><th>Started</th><th>Finished</th><th>Attempts</th><th>Error</th><th>YouTube</th><th>Action</th></tr></thead><tbody>${rows.map(job => {
    const selected = selectedPublishJobIds.has(Number(job.id));
    const youtubeLink = job.youtube_url ? `<a class="btn-mini" href="${esc(job.youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>` : '—';
    const clipPath = job.clip_output_path || job.video_source_path || '';
    const profile = job.profile_name ? `<div class="mono dim">${esc(job.profile_name)}</div>` : '';
    const err = job.error ? `<button class="link-video err mono" title="${esc(job.error)}" onclick="showPublishJobError(${Number(job.id)})">${esc(shortErrorText(job.error))}</button><button class="btn-mini" onclick="copyPublishJobError(${Number(job.id)})">Копировать</button>` : '—';
    const actions = [];
    if (job.can_retry) actions.push(`<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Retry</button>`);
    if (job.can_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)})">Run now</button>`);
    if (job.can_cancel) actions.push(`<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Cancel</button>`);
    actions.push(mpvButton(clipPath, 'MPV'));
    return `<tr><td><input type="checkbox" ${selected ? 'checked' : ''} onclick="togglePublishJobSelection(${Number(job.id)}, this.checked)"></td><td class="mono dim">#${job.id}</td><td>${badge(job.status)}</td><td class="mono mid ov" title="${esc(job.title || '')}">${esc(job.title || '—')}</td><td><div class="mono txt">${esc(job.channel_title || job.account_display_name || '—')}</div>${profile}</td><td class="mono dim">${esc(job.privacy_status || 'private')}</td><td class="mono dim">${esc(job.publish_mode || 'private')}</td><td class="mono dim ov" title="${esc(clipPath)}">${esc(shortPath(clipPath || '—'))}</td><td class="mono dim">${esc(formatMtime(job.created_at))}</td><td class="mono dim">${esc(formatMtime(job.started_at))}</td><td class="mono dim">${esc(formatMtime(job.finished_at))}</td><td class="mono txt">${esc(job.attempt_count || 0)}</td><td><div class="row-actions">${err}</div></td><td>${youtubeLink}</td><td><div class="row-actions">${actions.join('')}</div></td></tr>`;
  }).join('')}</tbody></table>`;
  renderPublishJobSelectionState();
}

function renderPublishView() {
  renderPublishConnectButton();
  renderPublishStatePanel();
  renderPublishAccountsPanel();
  renderPublishProfileSelect();
  renderPublishAccountSelect();
  renderReadyPublishClips();
  renderSelectedPublishClip();
  renderPublishJobsTable();
  onPublishModeChange();
  updatePublishButtons();
}

async function loadPublishView(options = {}) {
  const {silent = false} = options;
  renderPublishError('');
  try {
    const [profilesData, accountsData, clipsData, jobsData] = await Promise.all([
      api.get('/api/publish/youtube/oauth-profiles'),
      api.get('/api/publish/youtube/accounts'),
      api.get('/api/clips?status=done&limit=200'),
      api.get('/api/publish/jobs?limit=200'),
    ]);
    lastYoutubeProfiles = profilesData.profiles || [];
    lastYoutubeAccounts = accountsData.accounts || [];
    lastReadyPublishClips = clipsData.clips || [];
    lastPublishJobs = jobsData.jobs || [];
    syncPublishSelections();
    renderPublishView();
  } catch (err) {
    if (!silent) renderPublishError(`Не удалось загрузить публикацию:\n${err.message || err}`);
  }
}

async function refreshPublishJobs() {
  try {
    const data = await api.get('/api/publish/jobs?limit=200');
    lastPublishJobs = data.jobs || [];
    renderPublishJobsTable();
    if (currentView === 'clips') {
      await refreshWorkspaceList();
      renderWorkspaceListAndDetail();
    }
  } catch (err) {
    renderPublishError(`Не удалось загрузить очередь публикации:\n${err.message || err}`);
  }
}

function selectedPublishJobList() {
  return Array.from(selectedPublishJobIds)
    .map(id => lastPublishJobs.find(job => Number(job.id) === Number(id)))
    .filter(Boolean);
}
function nextRunnablePublishJobs(limit) {
  return lastPublishJobs
    .filter(job => job.status === 'queued')
    .sort((a, b) => Number(a.id) - Number(b.id))
    .slice(0, Number(limit || 0));
}

async function runPublishWorkerBatch(limit = null) {
  const batchLimit = Number(limit || document.getElementById('publish-batch-size')?.value || publishBatchSize || 3);
  const jobs = nextRunnablePublishJobs(batchLimit);
  if (!confirmPublishJobsBatch(jobs, batchLimit, 'запустить загрузку для')) return;
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/worker/run-once', {limit: batchLimit});
    showToast(`Обработано publish jobs: ${data.processed || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось обработать очередь публикации:\n${err.message || err}`);
  }
}

async function runSelectedPublishJobs() {
  const jobs = selectedPublishJobList().filter(job => job.can_run);
  if (!jobs.length) {
    showToast('Среди выбранных нет jobs для запуска', 'err');
    return;
  }
  if (!confirmPublishJobsBatch(jobs, jobs.length, 'запустить загрузку для')) return;
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/jobs/bulk-run', {job_ids: jobs.map(job => Number(job.id))});
    showToast(`Запущено: ${data.summary?.processed || 0} · ошибок: ${data.summary?.errors || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось запустить выбранные jobs:\n${err.message || err}`);
  }
}

async function retryFailedPublishJobs() {
  const selected = selectedPublishJobList().filter(job => job.status === 'failed' || job.status === 'cancelled');
  const jobs = selected.length ? selected : getVisiblePublishJobs().filter(job => job.status === 'failed');
  if (!jobs.length) {
    showToast('Failed jobs не найдены', 'err');
    return;
  }
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/jobs/bulk-retry', {job_ids: jobs.map(job => Number(job.id))});
    showToast(`Возвращено в очередь: ${data.summary?.updated || 0} · пропущено: ${data.summary?.skipped || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось повторить failed jobs:\n${err.message || err}`);
  }
}

async function cancelSelectedPublishJobs() {
  const jobs = selectedPublishJobList().filter(job => job.status === 'queued' || job.status === 'failed');
  if (!jobs.length) {
    showToast('Среди выбранных нет queued/failed jobs', 'err');
    return;
  }
  if (!confirm(`Отменить выбранные publish jobs: ${jobs.length}?`)) return;
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/jobs/bulk-cancel', {job_ids: jobs.map(job => Number(job.id))});
    showToast(`Отменено: ${data.summary?.updated || 0} · пропущено: ${data.summary?.skipped || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось отменить jobs:\n${err.message || err}`);
  }
}

function hideDonePublishJobs() {
  for (const job of lastPublishJobs) {
    if (job.status === 'done') hiddenDonePublishJobIds.add(Number(job.id));
  }
  renderPublishJobsTable();
  showToast('Done jobs скрыты из вида');
}

async function startYouTubeConnect() {
  const selectedProfile = getSelectedProfile();
  if (!selectedProfile) {
    const message = 'OAuth client не найден. Создайте OAuth Profile в настройках.';
    publishState.onboardingHint = message;
    renderPublishView();
    renderPublishError(message);
    showToast(message, 'err');
    return;
  }
  const popup = window.open('about:blank', 'shortsfarm_youtube_oauth');
  if (popup) {
    try {
      popup.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>ShortsFarm · YouTube OAuth</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f4f5;color:#18181b;font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{padding:24px 28px;border:1px solid #d4d4d8;border-radius:14px;background:#fff;box-shadow:0 20px 40px rgba(0,0,0,.08)}h1{margin:0 0 8px;font-size:20px}p{margin:0;color:#52525b}</style></head><body><main><h1>Открываю Google OAuth…</h1><p>Подождите пару секунд.</p></main></body></html>`);
      popup.document.close();
    } catch {}
  }
  renderPublishError('');
  publishState.busy = true;
  renderPublishConnectButton();
  try {
    const data = await api.post('/api/publish/youtube/connect/start', {oauth_profile_id: Number(selectedProfile.id)});
    if (!data?.auth_url) throw new Error('Google OAuth URL не получен');
    if (popup && !popup.closed) {
      popup.location.href = data.auth_url;
      popup.focus?.();
      showToast('Открываю Google Consent Screen');
    } else {
      showToast('Браузер заблокировал новую вкладку. Открываю авторизацию в текущем окне.', 'warn');
      window.location.href = data.auth_url;
    }
  } catch (err) {
    if (popup && !popup.closed) {
      popup.close();
    }
    const message = err.message || 'Не удалось начать подключение YouTube';
    renderPublishError(message);
    showToast(message, 'err');
  } finally {
    publishState.busy = false;
    renderPublishConnectButton();
  }
}

async function disconnectYouTubeAccount(accountId) {
  if (!accountId) return;
  renderPublishError('');
  try {
    await api.post(`/api/publish/youtube/accounts/${accountId}/disconnect`, {});
    showToast('YouTube канал отключён');
    await loadPublishView({silent: true});
  } catch (err) {
    renderPublishError(`Не удалось отключить канал:\n${err.message || err}`);
  }
}

function setSettingsTab(id, btn) {
  document.querySelectorAll('[data-settings-tab]').forEach(item => item.classList.remove('on'));
  if (btn) btn.classList.add('on');
  document.querySelectorAll('.settings-tab').forEach(item => item.classList.remove('on'));
  const panel = document.getElementById('settings-' + id);
  if (panel) panel.classList.add('on');
}

function openYouTubeSettings() {
  const btn = document.querySelector('[data-v="settings"]');
  nav('settings', btn);
  setSettingsTab('youtube-oauth', document.querySelector('[data-settings-tab="youtube-oauth"]'));
}

function settingsProfileById(profileId) {
  return (lastYoutubeProfiles || []).find(profile => Number(profile.id) === Number(profileId)) || null;
}

function showSettingsError(message) {
  hideInlineOk('settings-ok');
  if (message) showInlineError('settings-error', message);
  else hideInlineError('settings-error');
}

function showSettingsOk(message) {
  hideInlineError('settings-error');
  if (message) showInlineOk('settings-ok', message);
  else hideInlineOk('settings-ok');
}

function setOAuthManualMode(mode) {
  oauthManualMode = mode === 'manual' ? 'manual' : 'json';
  const jsonWrap = document.getElementById('settings-oauth-json-wrap');
  const manualWrap = document.getElementById('settings-oauth-manual-wrap');
  const title = document.getElementById('settings-oauth-form-title');
  const saveBtn = document.getElementById('settings-oauth-save-btn');
  if (jsonWrap) jsonWrap.style.display = oauthManualMode === 'json' ? 'block' : 'none';
  if (manualWrap) manualWrap.style.display = oauthManualMode === 'manual' ? 'block' : 'none';
  if (title) title.textContent = editingOAuthProfileId ? 'Редактирование OAuth Profile' : (oauthManualMode === 'json' ? 'Импорт OAuth Client JSON' : 'Ручной OAuth client');
  if (saveBtn) saveBtn.textContent = editingOAuthProfileId ? 'Сохранить OAuth Profile' : 'Сохранить OAuth client';
}

function startNewOAuthProfile() {
  editingOAuthProfileId = null;
  ['settings-oauth-name', 'settings-oauth-json', 'settings-oauth-client-id', 'settings-oauth-client-secret'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.value = '';
      el.disabled = false;
    }
  });
  const redirect = document.getElementById('settings-oauth-redirect-uri');
  if (redirect) {
    redirect.value = 'http://127.0.0.1:8000/api/publish/youtube/oauth/callback';
    redirect.disabled = false;
  }
  const isDefault = document.getElementById('settings-oauth-default');
  if (isDefault) {
    isDefault.checked = true;
    isDefault.disabled = false;
  }
  const deleteBtn = document.getElementById('settings-oauth-delete-btn');
  if (deleteBtn) deleteBtn.style.display = 'none';
  const status = document.getElementById('settings-oauth-status');
  if (status) status.textContent = '';
  setOAuthManualMode('json');
}

function editOAuthProfile(profileId) {
  const profile = settingsProfileById(profileId);
  if (!profile) return;
  editingOAuthProfileId = Number(profile.id);
  setOAuthManualMode('manual');
  const canEditCredentials = !isEnvOAuthProfile(profile);
  const fields = {
    'settings-oauth-name': profile.name || '',
    'settings-oauth-client-id': profile.client_id || '',
    'settings-oauth-client-secret': '',
    'settings-oauth-redirect-uri': profile.redirect_uri || 'http://127.0.0.1:8000/api/publish/youtube/oauth/callback',
  };
  Object.entries(fields).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  ['settings-oauth-client-id', 'settings-oauth-client-secret', 'settings-oauth-redirect-uri'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = !canEditCredentials;
  });
  const isDefault = document.getElementById('settings-oauth-default');
  if (isDefault) isDefault.checked = Boolean(profile.is_default);
  const deleteBtn = document.getElementById('settings-oauth-delete-btn');
  if (deleteBtn) deleteBtn.style.display = isEnvOAuthProfile(profile) ? 'none' : 'inline-flex';
  const status = document.getElementById('settings-oauth-status');
  if (status) {
    const secret = profile.client_secret_set ? 'client_secret сохранён' : 'client_secret не задан';
    status.textContent = `${oauthProfileSourceLabel(profile)} · ${secret}`;
  }
}

function renderOAuthProfilesSettings() {
  const el = document.getElementById('settings-oauth-profiles');
  if (!el) return;
  const rows = lastYoutubeProfiles || [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty">OAuth Profiles пока нет. Импортируйте OAuth Client JSON или заполните client_id/client_secret вручную.</div>';
    return;
  }
  el.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Название</th><th>Источник</th><th>Redirect URI</th><th>Статус</th><th>Действие</th></tr></thead><tbody>${rows.map(profile => {
    const secret = profile.client_secret_set ? 'secret ok' : 'secret missing';
    const mode = `${oauthProfileSourceLabel(profile)}${profile.is_default ? ' · default' : ''}`;
    const actions = [
      `<button class="btn-mini" onclick="editOAuthProfile(${Number(profile.id)})">Edit</button>`,
      profile.is_default ? '' : `<button class="btn-mini" onclick="setDefaultOAuthProfile(${Number(profile.id)})">Default</button>`,
      isEnvOAuthProfile(profile) ? '' : `<button class="btn-danger" onclick="deleteOAuthProfile(${Number(profile.id)})">Delete</button>`,
    ].filter(Boolean).join('');
    return `<tr><td class="mono dim">#${profile.id}</td><td><div class="mono txt">${esc(profile.name || `Profile #${profile.id}`)}</div><div class="mono dim">${esc(profile.client_id || '')}</div></td><td class="mono dim">${esc(mode)} · ${esc(secret)}</td><td class="mono dim ov">${esc(profile.redirect_uri || '—')}</td><td>${badge(profile.status || 'active')}</td><td><div class="row-actions">${actions}</div></td></tr>`;
  }).join('')}</tbody></table>`;
}

async function loadSettingsView(options = {}) {
  const {silent = false} = options;
  if (!silent) showSettingsError('');
  try {
    const data = await api.get('/api/publish/youtube/oauth-profiles');
    lastYoutubeProfiles = data.profiles || [];
    renderOAuthProfilesSettings();
    renderPublishView();
    if (!editingOAuthProfileId && currentView === 'settings') {
      const name = document.getElementById('settings-oauth-name');
      const json = document.getElementById('settings-oauth-json');
      if (name && json && !name.value && !json.value) startNewOAuthProfile();
    }
  } catch (err) {
    if (!silent) showSettingsError(`Не удалось загрузить YouTube OAuth:\n${err.message || err}`);
  }
}

async function saveOAuthProfile() {
  showSettingsError('');
  showSettingsOk('');
  const name = document.getElementById('settings-oauth-name')?.value.trim() || '';
  const isDefault = Boolean(document.getElementById('settings-oauth-default')?.checked);
  const redirectUri = document.getElementById('settings-oauth-redirect-uri')?.value.trim() || 'http://127.0.0.1:8000/api/publish/youtube/oauth/callback';
  try {
    let data;
    if (editingOAuthProfileId) {
      const profile = settingsProfileById(editingOAuthProfileId);
      const payload = {
        name: name || profile?.name || `Profile #${editingOAuthProfileId}`,
        redirect_uri: redirectUri,
        status: profile?.status || 'active',
      };
      if (!isEnvOAuthProfile(profile)) {
        payload.client_id = document.getElementById('settings-oauth-client-id')?.value.trim() || profile?.client_id || '';
        const secret = document.getElementById('settings-oauth-client-secret')?.value.trim() || null;
        if (secret) payload.client_secret = secret;
      }
      data = await api.patch(`/api/publish/youtube/oauth-profiles/${Number(editingOAuthProfileId)}`, payload);
      if (isDefault) {
        data = await api.post(`/api/publish/youtube/oauth-profiles/${Number(editingOAuthProfileId)}/set-default`, {});
      }
    } else if (oauthManualMode === 'json') {
      const jsonText = document.getElementById('settings-oauth-json')?.value || '';
      data = await api.post('/api/publish/youtube/oauth-profiles/import-client-json', {
        name,
        json_text: jsonText,
        is_default: isDefault,
      });
    } else {
      data = await api.post('/api/publish/youtube/oauth-profiles', {
        name: name || 'YouTube OAuth',
        client_id: document.getElementById('settings-oauth-client-id')?.value.trim() || '',
        client_secret: document.getElementById('settings-oauth-client-secret')?.value.trim() || '',
        redirect_uri: redirectUri,
        is_default: isDefault,
      });
    }
    const profile = data?.profile;
    showSettingsOk(`OAuth Profile сохранён${profile?.id ? `: #${profile.id}` : ''}`);
    editingOAuthProfileId = profile?.id ? Number(profile.id) : editingOAuthProfileId;
    await loadSettingsView({silent: true});
    if (editingOAuthProfileId) editOAuthProfile(editingOAuthProfileId);
  } catch (err) {
    showSettingsError(err.message || 'Не удалось сохранить OAuth Profile');
  }
}

async function setDefaultOAuthProfile(profileId) {
  showSettingsError('');
  try {
    await api.post(`/api/publish/youtube/oauth-profiles/${Number(profileId)}/set-default`, {});
    showSettingsOk(`OAuth Profile #${profileId} выбран по умолчанию`);
    await loadSettingsView({silent: true});
  } catch (err) {
    showSettingsError(err.message || 'Не удалось назначить OAuth Profile по умолчанию');
  }
}

async function deleteOAuthProfile(profileId = editingOAuthProfileId) {
  if (!profileId) return;
  if (!confirm(`Удалить OAuth Profile #${profileId}?`)) return;
  showSettingsError('');
  try {
    await api.del(`/api/publish/youtube/oauth-profiles/${Number(profileId)}`);
    showSettingsOk(`OAuth Profile #${profileId} удалён`);
    if (Number(editingOAuthProfileId) === Number(profileId)) startNewOAuthProfile();
    await loadSettingsView({silent: true});
  } catch (err) {
    showSettingsError(err.message || 'Не удалось удалить OAuth Profile');
  }
}

function onPublishProfileChange(value) {
  publishState.selectedProfileId = value ? Number(value) : null;
  syncPublishSelections();
  renderPublishView();
}

function onPublishAccountChange(value) {
  publishState.selectedAccountId = value ? Number(value) : null;
  renderPublishAccountSelect();
  updatePublishButtons();
}

function selectPublishClip(clipId) {
  publishState.selectedClipId = Number(clipId);
  const clip = getSelectedPublishClip();
  const titleInput = document.getElementById('publish-title');
  if (clip && titleInput && !titleInput.value.trim()) {
    const defaultTitle = clip.video_title || (clip.output_path || '').split('/').pop() || `clip-${clip.id}`;
    titleInput.value = defaultTitle;
  }
  renderReadyPublishClips();
  renderSelectedPublishClip();
  updatePublishButtons();
}

function onPublishModeChange() {
  const mode = document.getElementById('publish-mode')?.value || 'private';
  const field = document.getElementById('publish-at-field');
  if (field) field.style.display = mode === 'schedule' ? 'block' : 'none';
  updatePublishButtons();
}

function updatePublishButtons() {
  const enqueueBtn = document.getElementById('publish-enqueue-btn');
  const uploadBtn = document.getElementById('publish-upload-btn');
  const connectButtons = ['publish-connect-btn', 'publish-connect-btn-accounts']
    .map(id => document.getElementById(id))
    .filter(Boolean);
  const hasProfile = Boolean(getSelectedProfile());
  const hasAccount = Boolean(getSelectedAccount());
  const hasClip = Boolean(getSelectedPublishClip());
  const title = document.getElementById('publish-title')?.value.trim() || '';
  const category = document.getElementById('publish-category')?.value.trim() || '';
  const mode = document.getElementById('publish-mode')?.value || 'private';
  const publishAt = document.getElementById('publish-at')?.value.trim() || '';
  const valid = hasProfile && hasAccount && hasClip && Boolean(title) && Boolean(category) && (mode !== 'schedule' || Boolean(publishAt));
  if (enqueueBtn) enqueueBtn.disabled = publishState.busy || !valid;
  if (uploadBtn) uploadBtn.disabled = publishState.busy || !valid;
  connectButtons.forEach(btn => {
    btn.disabled = publishState.busy || !hasProfile;
  });
}

function publishRequestBody() {
  return {
    account_id: Number(publishState.selectedAccountId),
    title: document.getElementById('publish-title')?.value || '',
    description: document.getElementById('publish-description')?.value || '',
    tags: (document.getElementById('publish-tags')?.value || '').split(',').map(item => item.trim()).filter(Boolean),
    category_id: document.getElementById('publish-category')?.value || '22',
    publish_mode: document.getElementById('publish-mode')?.value || 'private',
    publish_at: document.getElementById('publish-mode')?.value === 'schedule'
      ? (document.getElementById('publish-at')?.value || '')
      : null,
    made_for_kids: Boolean(document.getElementById('publish-made-for-kids')?.checked),
  };
}

async function submitPublish(mode) {
  const clip = getSelectedPublishClip();
  if (!clip) {
    showInlineError('publish-form-error', 'Сначала выберите готовый клип.');
    return;
  }
  hideInlineError('publish-form-error');
  hideInlineOk('publish-form-ok');
  const body = publishRequestBody();
  if (!confirmYoutubeBatch(1, body.publish_mode, mode === 'upload' ? 'загрузить' : 'отправить')) return;
  publishState.busy = true;
  updatePublishButtons();
  try {
    const endpoint = mode === 'upload'
      ? `/api/publish/youtube/clips/${Number(clip.id)}/upload`
      : `/api/publish/youtube/clips/${Number(clip.id)}/enqueue`;
    const data = await api.post(endpoint, body);
    const job = data.job || {};
    const message = mode === 'upload'
      ? `Видео загружено: ${job.youtube_url || 'YouTube URL пока не получен'}`
      : `Задача #${job.id} добавлена в очередь публикации`;
    showInlineOk('publish-form-ok', message);
    showToast(mode === 'upload' ? 'Клип загружен в YouTube' : 'Клип добавлен в очередь');
    await loadPublishView({silent: true});
  } catch (err) {
    showInlineError('publish-form-error', err.message || 'Не удалось отправить публикацию');
  } finally {
    publishState.busy = false;
    updatePublishButtons();
  }
}

async function retryPublishJob(jobId) {
  renderPublishError('');
  try {
    await api.post(`/api/publish/jobs/${jobId}/retry`, {});
    showToast(`Publish job #${jobId} возвращён в очередь`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось повторить job #${jobId}:\n${err.message || err}`);
  }
}

async function cancelPublishJob(jobId) {
  renderPublishError('');
  try {
    await api.post(`/api/publish/jobs/${jobId}/cancel`, {});
    showToast(`Publish job #${jobId} отменён`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось отменить job #${jobId}:\n${err.message || err}`);
  }
}

async function runPublishJob(jobId) {
  renderPublishError('');
  try {
    await api.post(`/api/publish/jobs/${jobId}/run`, {});
    showToast(`Publish job #${jobId} выполнен`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось запустить job #${jobId}:\n${err.message || err}`);
  }
}

async function runPublishWorkerOnce() {
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/worker/run-once', {limit: 3});
    showToast(`Обработано publish jobs: ${data.processed || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось обработать очередь публикации:\n${err.message || err}`);
  }
}

function handleOAuthEvent(payload) {
  const ok = Boolean(payload?.ok);
  const message = payload?.message || '';
  if (ok) {
    publishState.onboardingHint = '';
    showToast('YouTube канал подключён. Обновляю список каналов...');
  } else {
    showToast(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.', 'err');
    if (currentView === 'publish') {
      renderPublishError(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.');
    }
  }
  loadSettingsView({silent: true});
  loadPublishView({silent: true});
}

async function loadOutputs() {
  try {
    const data = await api.get('/api/outputs');
    lastOutputs = data.outputs || [];
  } catch {
    lastOutputs = [];
  }
}

window.addEventListener('DOMContentLoaded', () => {
  setSecs(60);
  setMode('file');
  loadDashboard();
  loadJobs();
  loadVideos();
  loadClips();
  loadOutputs();
  loadPublishView();
  initFsBrowser();
  onPublishModeChange();
});
window.addEventListener('message', event => {
  if (event.origin !== window.location.origin) return;
  if (event.data?.type === 'shortsfarm-youtube-oauth-complete' || event.data?.type === 'shortsfarm-youtube-oauth-error') {
    handleOAuthEvent(event.data);
  }
});
window.addEventListener('storage', event => {
  if (event.key === 'shortsfarm.youtube.oauth.event' && event.newValue) {
    try {
      handleOAuthEvent(JSON.parse(event.newValue));
    } catch {
      handleOAuthEvent({ok: false, message: 'OAuth окно вернуло ошибку. Попробуйте снова.'});
    }
  }
});
setInterval(() => {
  if (currentView === 'dashboard') loadDashboard();
  if (currentView === 'queue') loadJobs();
  if (currentView === 'clips') loadClips();
  if (currentView === 'publish') loadPublishView({silent: true});
}, 5000);
