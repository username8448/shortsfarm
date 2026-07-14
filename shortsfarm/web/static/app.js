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
const WORKSPACE_SYSTEM_FOLDERS = ['sources','cuts','prepared','edits','ready','published'];
const WORKSPACE_FOLDER_LABELS = {
  sources: 'Исходники',
  cuts: 'Нарезки',
  prepared: 'Подготовленные',
  edits: 'Результаты монтажа',
  ready: 'Готовые',
  published: 'Опубликованные',
};
const WORKSPACE_KIND_LABELS = {
  system: 'системная папка',
  custom: 'папка',
  collection: 'коллекция',
  project: 'проект',
  file: 'файл',
  video: 'видео',
  image: 'изображение',
  other: 'файл',
};
function workspaceFolderLabel(pathOrName, fallback = '') {
  const raw = String(pathOrName || fallback || '');
  const first = raw.split('/').filter(Boolean)[0] || raw;
  return WORKSPACE_FOLDER_LABELS[first] || fallback || raw;
}
function workspaceDisplayPath(path) {
  const raw = String(path || '');
  if (!raw) return '';
  const parts = raw.split('/').filter(Boolean);
  if (!parts.length) return raw;
  const [first, ...rest] = parts;
  return [WORKSPACE_FOLDER_LABELS[first] || first, ...rest].join('/');
}
function workspaceKindLabel(kind) {
  return WORKSPACE_KIND_LABELS[String(kind || '').toLowerCase()] || kind || 'файл';
}
function fileNameFromPath(value) {
  const name = String(value || '').split(/[\\/]/).filter(Boolean).pop() || '';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(0, dot) : name;
}
async function pickLocalPath({kind='file', title='Выберите файл', buttonId='', errorId=''}) {
  const button = buttonId ? document.getElementById(buttonId) : null;
  if (button) button.disabled = true;
  try {
    const data = await api.post('/api/local-dialogs/pick', {kind, title});
    return data?.selected ? (data.path || '') : '';
  } catch (err) {
    const message = err.message || (
      kind === 'file'
        ? 'Локальный выбор файла недоступен. Укажите путь вручную.'
        : 'Локальный выбор папки недоступен. Укажите путь вручную.'
    );
    showToast(message, 'err');
    if (errorId) showInlineError(errorId, message);
    return '';
  } finally {
    if (button) button.disabled = false;
  }
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
function videoWatchThumb(path, name='video') {
  if (!path) return videoThumb(path, name);
  return `<button class="video-watch-trigger" data-path="${esc(path)}" title="Смотреть: ${esc(name)}" onclick="event.stopPropagation();openWebPlayer(this.dataset.path,{title:this.dataset.title||''})" data-title="${esc(name)}">${videoThumb(path, name)}</button>`;
}
const WORKSPACE_MEDIA_SECTIONS = ['sources', 'cuts', 'prepared', 'edits', 'ready', 'published'];
const videoLightboxState = {
  path: '',
  title: '',
  mode: 'viewer',
};
function isWorkspaceRelativeMediaPath(path) {
  const text = String(path || '').trim();
  if (!text || text.startsWith('/') || /^[A-Za-z]:[\\/]/.test(text) || text.includes('\\')) return false;
  const parts = text.split('/');
  return WORKSPACE_MEDIA_SECTIONS.includes(parts[0]) && !parts.some(part => !part || part === '.' || part === '..');
}
async function ensureWorkspaceRootForPlayer() {
  const cached = window.ShortsFarmFiles?.getWorkspaceRoot?.();
  if (cached) return cached;
  const settings = await api.get('/api/settings/workspace');
  const root = settings.workspace_root || null;
  window.ShortsFarmFiles?.setWorkspaceRoot?.(root);
  return root;
}
async function workspaceMediaPathForPlayer(path) {
  const text = String(path || '').trim();
  if (!text) throw new Error('Путь к видео не задан.');
  if (isWorkspaceRelativeMediaPath(text)) return text;
  const root = String(await ensureWorkspaceRootForPlayer() || '').replace(/\/+$/, '');
  if (!root) throw new Error('workspace_root не настроен.');
  const normalized = text.replace(/\/+$/, '');
  if (normalized === root || !normalized.startsWith(root + '/')) {
    throw new Error('Web player открывает только видео внутри workspace. Сначала импортируйте файл в workspace.');
  }
  const relative = normalized.slice(root.length + 1);
  if (!isWorkspaceRelativeMediaPath(relative)) {
    throw new Error('Web player открывает только видео из sources/cuts/prepared/edits/ready/published.');
  }
  return relative;
}
async function openWebPlayer(path, options = {}) {
  try {
    const relative = await workspaceMediaPathForPlayer(path);
    if (
      typeof window.shortsFarmOpenVideoLightbox === 'function'
      && window.shortsFarmOpenVideoLightbox(relative, options) !== false
    ) {
      return;
    }
    window.open(`/player?path=${encodeURIComponent(relative)}`, '_blank', 'noopener,noreferrer');
  } catch (err) {
    showToast(err.message || 'Не удалось открыть web player', 'err');
    if (currentView === 'split') showInlineError('split-error', err.message || 'Не удалось открыть web player');
    if (currentView === 'files') showInlineError('files-error', err.message || 'Не удалось открыть web player');
  }
}
function videoLightboxUrl(path, mode = 'viewer') {
  return `/player?path=${encodeURIComponent(path)}&embed=1&mode=${encodeURIComponent(mode)}`;
}
function ensureVideoLightbox() {
  let box = document.getElementById('video-lightbox');
  if (box && document.getElementById('video-lightbox-frame')) return box;
  if (box) box.remove();
  box = document.createElement('div');
  box.id = 'video-lightbox';
  box.className = 'video-lightbox';
  box.innerHTML = `
    <div class="video-lightbox-backdrop" data-lightbox-close="1"></div>
    <section class="video-lightbox-panel" role="dialog" aria-modal="true" aria-label="Video Player">
      <header class="video-lightbox-head">
        <div>
          <div class="video-lightbox-kicker">Video Player</div>
          <div class="video-lightbox-title" id="video-lightbox-title"></div>
          <div class="video-lightbox-path mono" id="video-lightbox-path"></div>
        </div>
        <div class="video-lightbox-actions">
          <button class="btn-mini" id="video-lightbox-copy">Копировать путь</button>
          <button class="btn-mini" id="video-lightbox-tools">Инструменты</button>
          <button class="btn-mini" id="video-lightbox-open">Открыть отдельно</button>
          <button class="btn-danger" id="video-lightbox-close" title="Закрыть">×</button>
        </div>
      </header>
      <iframe id="video-lightbox-frame" title="ShortsFarm video player" loading="lazy" allow="fullscreen; picture-in-picture"></iframe>
    </section>`;
  document.body.appendChild(box);
  box.addEventListener('click', event => {
    const target = event.target;
    if (target?.dataset?.lightboxClose) closeVideoLightbox();
  });
  document.getElementById('video-lightbox-close')?.addEventListener('click', closeVideoLightbox);
  document.getElementById('video-lightbox-copy')?.addEventListener('click', () => {
    if (!videoLightboxState.path) return;
    navigator.clipboard?.writeText(videoLightboxState.path);
    showToast('Workspace path скопирован');
  });
  document.getElementById('video-lightbox-open')?.addEventListener('click', () => {
    if (!videoLightboxState.path) return;
    window.open(`/player?path=${encodeURIComponent(videoLightboxState.path)}`, '_blank', 'noopener,noreferrer');
  });
  document.getElementById('video-lightbox-tools')?.addEventListener('click', () => {
    videoLightboxState.mode = videoLightboxState.mode === 'workbench' ? 'viewer' : 'workbench';
    updateVideoLightboxFrame();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && box?.classList.contains('open')) closeVideoLightbox();
  });
  return box;
}
function updateVideoLightboxFrame() {
  const frame = document.getElementById('video-lightbox-frame');
  const tools = document.getElementById('video-lightbox-tools');
  if (frame) frame.src = videoLightboxUrl(videoLightboxState.path, videoLightboxState.mode);
  if (tools) tools.textContent = videoLightboxState.mode === 'workbench' ? 'Скрыть инструменты' : 'Инструменты';
}
function stopVideoLightboxPlayback(frame) {
  if (!frame) return;
  try {
    frame.contentWindow?.postMessage({type: 'shortsfarm:pause-video'}, window.location.origin);
  } catch (_) {
    // Ignore cross-document teardown races.
  }
  try {
    frame.contentWindow?.document?.querySelectorAll('video,audio').forEach(media => {
      media.pause();
      media.removeAttribute('src');
      media.load();
    });
  } catch (_) {
    // The fallback below still tears down the iframe.
  }
  try {
    frame.src = 'about:blank';
    frame.removeAttribute('src');
    const replacement = frame.cloneNode(false);
    replacement.id = 'video-lightbox-frame';
    replacement.title = 'ShortsFarm video player';
    replacement.loading = 'lazy';
    replacement.setAttribute('allow', 'fullscreen; picture-in-picture');
    replacement.src = 'about:blank';
    frame.replaceWith(replacement);
  } catch (_) {
    frame.src = 'about:blank';
  }
}
function closeVideoLightbox() {
  const box = document.getElementById('video-lightbox');
  const frame = document.getElementById('video-lightbox-frame');
  if (box) box.classList.remove('open');
  stopVideoLightboxPlayback(frame);
  document.body.classList.remove('video-lightbox-open');
}
function showVideoLightbox(path, options = {}) {
  if (!isWorkspaceRelativeMediaPath(path)) {
    throw new Error('Lightbox открывает только видео внутри workspace.');
  }
  const box = ensureVideoLightbox();
  videoLightboxState.path = path;
  videoLightboxState.title = String(options.title || path.split('/').pop() || 'Video Player');
  videoLightboxState.mode = options.startMode === 'workbench' ? 'workbench' : 'viewer';
  const title = document.getElementById('video-lightbox-title');
  const pathEl = document.getElementById('video-lightbox-path');
  if (title) title.textContent = videoLightboxState.title;
  if (pathEl) pathEl.textContent = path;
  updateVideoLightboxFrame();
  box.classList.add('open');
  document.body.classList.add('video-lightbox-open');
}
window.shortsFarmOpenVideoLightbox = (workspacePath, options = {}) => {
  try {
    showVideoLightbox(String(workspacePath || ''), options);
    return true;
  } catch (err) {
    showToast(err.message || 'Не удалось открыть lightbox', 'err');
    return false;
  }
};
function webPlayerButton(path, label='Смотреть') {
  if (!path) return `<button class="btn-mini" disabled>${esc(label)}</button>`;
  return `<button class="btn-mini" data-web-player="1" data-path="${esc(path)}" onclick="event.stopPropagation();openWebPlayer(this.dataset.path)">${esc(label)}</button>`;
}
function mpvButton(path, label='Смотреть') {
  return webPlayerButton(path, label);
}
function outputFolderButton(path, label='Папка') {
  if (!path) return '<button class="btn-mini" disabled>Папка</button>';
  return `<button class="btn-mini" data-path="${esc(path)}" onclick="event.stopPropagation();goToOutputFolder(this.dataset.path)">${esc(label)}</button>`;
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
    disabled:'отключён',
    cancelled:'отменено',
    pending:'ожидает проверки',
    approved:'одобрено',
    rejected:'отклонено',
    disconnected:'отключён',
    expired:'истёк',
    error:'ошибка'
  }[value] || value || '—');
}
function badgeClass(status) {
  return status === 'done' || status === 'reviewed' || status === 'ok' || status === 'active' || status === 'ready' || status === 'uploaded' || status === 'approved'
    ? 'b-ok'
    : status === 'failed' || status === 'error' || status === 'rejected'
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

let textActionModalState = null;
let storageProfilePickModalState = null;

function openTextActionModal(options = {}) {
  return new Promise(resolve => {
    textActionModalState = {
      resolve,
      validate: options.validate || null,
    };
    const modal = document.getElementById('ui-text-modal');
    const title = document.getElementById('ui-text-modal-title');
    const label = document.getElementById('ui-text-modal-label');
    const input = document.getElementById('ui-text-modal-input');
    const hint = document.getElementById('ui-text-modal-hint');
    const confirm = document.getElementById('ui-text-modal-confirm');
    hideInlineError('ui-text-modal-error');
    if (title) title.textContent = options.title || 'Действие';
    if (label) label.textContent = options.label || 'Название';
    if (hint) hint.textContent = options.hint || '';
    if (confirm) confirm.textContent = options.confirmText || 'Сохранить';
    if (input) {
      input.value = options.value || '';
      input.placeholder = options.placeholder || '';
      input.maxLength = options.maxLength || 255;
    }
    if (modal) modal.style.display = 'grid';
    setTimeout(() => {
      input?.focus();
      input?.select();
    }, 0);
  });
}

function resolveTextActionModal(value) {
  const state = textActionModalState;
  textActionModalState = null;
  const modal = document.getElementById('ui-text-modal');
  if (modal) modal.style.display = 'none';
  hideInlineError('ui-text-modal-error');
  state?.resolve(value);
}

function closeTextActionModal(event) {
  if (event && event.target && event.target.id !== 'ui-text-modal') return;
  resolveTextActionModal(null);
}

function confirmTextActionModal() {
  const value = (document.getElementById('ui-text-modal-input')?.value || '').trim();
  const error = textActionModalState?.validate ? textActionModalState.validate(value) : (!value ? 'Введите значение.' : '');
  if (error) {
    showInlineError('ui-text-modal-error', error);
    return;
  }
  resolveTextActionModal(value);
}

function openStorageProfilePickModal(profiles = []) {
  return new Promise(resolve => {
    storageProfilePickModalState = {resolve};
    const select = document.getElementById('storage-profile-pick-select');
    hideInlineError('storage-profile-pick-error');
    if (select) {
      select.innerHTML = profiles.map(profile => (
        `<option value="${Number(profile.id)}">#${Number(profile.id)} · ${esc(profile.name || profile.handle || 'Профиль')}</option>`
      )).join('');
    }
    const modal = document.getElementById('storage-profile-pick-modal');
    if (modal) modal.style.display = 'grid';
    setTimeout(() => select?.focus(), 0);
  });
}

function resolveStorageProfilePickModal(value) {
  const state = storageProfilePickModalState;
  storageProfilePickModalState = null;
  const modal = document.getElementById('storage-profile-pick-modal');
  if (modal) modal.style.display = 'none';
  hideInlineError('storage-profile-pick-error');
  state?.resolve(value);
}

function closeStorageProfilePickModal(event) {
  if (event && event.target && event.target.id !== 'storage-profile-pick-modal') return;
  resolveStorageProfilePickModal(null);
}

function confirmStorageProfilePickModal() {
  const profileId = Number(document.getElementById('storage-profile-pick-select')?.value || 0);
  if (!profileId) {
    showInlineError('storage-profile-pick-error', 'Выберите профиль.');
    return;
  }
  resolveStorageProfilePickModal(profileId);
}

let currentView = 'dashboard';
const VIEW_TITLES = {
  dashboard: 'Панель',
  pipeline: 'Конвейер',
  files: 'Файлы',
  split: 'Нарезка',
  queue: 'Очередь',
  clips: 'Клипы',
  tags: 'Теги',
  'storage-profiles': 'Профили',
  'storage-profile': 'Профиль',
  integrations: 'Интеграции',
  studio: 'Template Studio',
  settings: 'Настройки',
};
const uiState = {
  density: 'compact',
  sidebarCollapsed: false,
  queueViewMode: 'table',
  videoViewMode: 'table',
  clipViewMode: 'table',
};
let currentPublishTab = 'youtube';
let secsVal = 60;
let splitMode = 'file';
const skipList = [];
let lastJobs = [];
let lastQueueItems = [];
let lastVideos = [];
let selectedVideoIds = new Set();
let pendingVideoDeleteIds = [];
let queueQuickFilter = 'all';
let queueKindFilter = 'all';
let queueStatusFilter = 'all';
let queueReviewFilter = 'all';
let queueSourceStateFilter = 'all';
let queueIncludeDeleted = false;
let queueSearchQuery = '';
let expandedQueueItemIds = new Set();
let videoFilter = 'all';
let lastClips = [];
let queueSubView = 'overview';
let workspaceFilter = 'all';
let workspaceSearchQuery = '';
let workspaceFilterIncludeTagIds = new Set();
let workspaceFilterExcludeTagIds = new Set();
let workspaceParentVideoFilter = null;
let selectedWorkspaceKeys = new Set();
let currentWorkspaceItemKey = null;
let workspaceDetailDirty = false;
let lastOutputs = [];
let lastYoutubeAccounts = [];
let lastYoutubeProfiles = [];
let lastPublishJobs = [];
let lastPublishScheduleGroups = [];
let lastReadyPublishClips = [];
let publishJobFilter = 'all';
let publishScheduleFilter = 'untimed';
let selectedPublishJobIds = new Set();
let hiddenDonePublishJobIds = new Set();
let publishBatchSize = 3;
let editingPublishScheduleGroupId = null;
let editingPublishScheduleJobIds = [];
let editingPublishScheduleInitial = null;
let editingReactions = [];
let editingPools = [];
let editingStudioTemplates = [];
let editingProfiles = [];
let editingAccounts = [];
let editingJobs = [];
let editingJobFilter = 'all';
let editingJobReviewFilter = 'all';
let selectedEditingJobIds = new Set();
let editingPreviewJobId = null;
let storageProfiles = [];
let currentStorageProfileId = null;
let currentStorageProfile = null;
let storageProfileItems = [];
let storageProfilePublishJobs = [];
let storageProfileYoutubeVideos = [];
let storageProfileActiveTab = 'publish';
let storageProfileDrawerOpen = false;
let storageProfileDrawerSection = 'publish';
let catalogTags = [];
let selectedStorageProfileItemIds = new Set();
let storageProfileCandidates = [];
let selectedStorageCandidatePaths = new Set();
let storageCatalogSearchQuery = '';
let storageCatalogSearchTimer = null;
let tagManagerVideoResults = [];
let selectedTagManagerVideoPaths = new Set();
let tagManagerSearchQuery = '';
let tagManagerSearchTimer = null;
let tagManagerTagQuery = '';
let storageYoutubeAccounts = [];
let storageCandidatePickerOpen = false;
let integrationsSearchQuery = '';
let pipelineSources = [];
let pipelineTemplates = [];
let pipelineReactions = [];
let pipelineReactionPools = [];
let pipelineRenderProfiles = [];
let pipelineRuns = [];
let pipelineHealth = null;
let pipelineActiveRunId = null;
let pipelinePollTimer = null;
const workspaceYoutubeState = {
  selectedAccountId: null,
  busy: false,
};

const workspaceEditingState = {
  selectedProfileId: null,
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

function readUiPreference(key, fallback) {
  try {
    const value = localStorage.getItem(`shortsfarm.ui.${key}`);
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}
function writeUiPreference(key, value) {
  try {
    localStorage.setItem(`shortsfarm.ui.${key}`, String(value));
  } catch {}
}
function applyDensity() {
  const density = uiState.density === 'comfortable' ? 'comfortable' : 'compact';
  document.body.classList.toggle('density-compact', density === 'compact');
  document.body.classList.toggle('density-comfortable', density === 'comfortable');
  const btn = document.getElementById('density-toggle');
  if (btn) btn.textContent = density === 'compact' ? 'Компактно' : 'Комфортно';
}
function setDensity(value) {
  uiState.density = value === 'comfortable' ? 'comfortable' : 'compact';
  writeUiPreference('density', uiState.density);
  applyDensity();
}
function toggleDensity() {
  setDensity(uiState.density === 'compact' ? 'comfortable' : 'compact');
}
function applySidebarState() {
  document.body.classList.toggle('sidebar-collapsed', Boolean(uiState.sidebarCollapsed));
}
function toggleSidebarCollapsed() {
  uiState.sidebarCollapsed = !uiState.sidebarCollapsed;
  writeUiPreference('sidebarCollapsed', uiState.sidebarCollapsed ? '1' : '0');
  applySidebarState();
}
function setTopbarTitle(id) {
  const el = document.getElementById('topbar-view-title');
  if (el) el.textContent = VIEW_TITLES[id] || id || 'ShortsFarm';
}
function setSegmentedState(selector, activeValue, attr) {
  document.querySelectorAll(selector).forEach(btn => {
    btn.classList.toggle('on', btn.getAttribute(attr) === activeValue);
  });
}
function initResponsiveShell() {
  uiState.density = readUiPreference('density', 'compact');
  uiState.sidebarCollapsed = readUiPreference('sidebarCollapsed', '0') === '1';
  uiState.queueViewMode = readUiPreference('queueViewMode', 'table') === 'grid' ? 'grid' : 'table';
  uiState.videoViewMode = readUiPreference('videoViewMode', 'table') === 'grid' ? 'grid' : 'table';
  uiState.clipViewMode = readUiPreference('clipViewMode', 'table') === 'grid' ? 'grid' : 'table';
  applyDensity();
  applySidebarState();
  setTopbarTitle(currentView);
  setSegmentedState('[data-queue-view]', uiState.queueViewMode, 'data-queue-view');
  setSegmentedState('[data-video-view]', uiState.videoViewMode, 'data-video-view');
  setSegmentedState('[data-clip-view]', uiState.clipViewMode, 'data-clip-view');
  try {
    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect?.width || window.innerWidth;
      document.body.classList.toggle('narrow-workspace', width < 1120);
    });
    observer.observe(document.getElementById('main') || document.body);
  } catch {}
}
function openInspector({title = 'Детали', kicker = 'Inspector', body = ''} = {}) {
  const titleEl = document.getElementById('inspector-title');
  const kickerEl = document.getElementById('inspector-kicker');
  const bodyEl = document.getElementById('inspector-body');
  if (titleEl) titleEl.textContent = title;
  if (kickerEl) kickerEl.textContent = kicker;
  if (bodyEl) bodyEl.innerHTML = body || '<div class="empty compact">Нет данных</div>';
  document.body.classList.add('inspector-open');
  document.getElementById('global-inspector')?.setAttribute('aria-hidden', 'false');
}
function closeInspector() {
  document.body.classList.remove('inspector-open');
  document.getElementById('global-inspector')?.setAttribute('aria-hidden', 'true');
}
function renderActionBar(title = '', actions = '') {
  const el = document.getElementById('global-action-bar');
  if (!el) return;
  if (!title || !actions) {
    el.classList.remove('on');
    el.innerHTML = '';
    return;
  }
  el.innerHTML = `<span class="bar-title">${esc(title)}</span><div class="bar-actions">${actions}</div>`;
  el.classList.add('on');
}

function activateView(id, btn) {
  currentView = id;
  setTopbarTitle(id);
  document.querySelectorAll('.v').forEach(el => el.classList.remove('on'));
  const view = document.getElementById('v-' + id);
  if (view) view.classList.add('on');
  document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
  if (btn) btn.classList.add('on');
  renderActionBar();
}

function nav(id, btn) {
  if (id === 'clips') id = 'queue';
  if (id === 'editing') id = 'studio';
  if (id === 'videos') {
    openQueueSources();
    return;
  }
  activateView(id, btn);
  if (id === 'dashboard') loadDashboard();
  if (id === 'files') window.loadManagedFiles?.();
  if (id === 'pipeline') loadPipelineView();
  if (id === 'split' && !fsState.currentPath) initFsBrowser();
  if (id === 'queue') {
    setQueueSubView('overview');
    loadJobs();
  }
  if (id === 'tags') loadTagsView();
  if (id === 'storage-profiles') {
    window.history.replaceState({}, '', storageProfileUrl(null));
    loadStorageProfiles();
  }
  if (id === 'integrations') loadIntegrationsView();
  if (id === 'settings') loadSettingsView();
}

function pipelineWorkspaceSourceItems() {
  const sections = pipelineSources?.sections || [];
  const sourceSection = sections.find(section => section.key === 'sources') || {};
  return sourceSection.items || [];
}
function pipelineSourceOptions() {
  const items = pipelineWorkspaceSourceItems();
  if (!items.length) return '<option value="">Нет видео в sources/</option>';
  return items.map(item => {
    const title = item.name || item.workspace_path;
    return `<option value="${esc(item.workspace_path)}">${esc(workspaceDisplayPath(item.workspace_path))} · ${esc(title)}</option>`;
  }).join('');
}
function pipelineTemplateOptions() {
  const active = (pipelineTemplates || []).filter(item => (item.status || 'active') === 'active');
  const items = active.length ? active : pipelineTemplates;
  if (!items.length) return '<option value="">Нет Studio templates</option>';
  return items.map(item => `<option value="${Number(item.id)}">${esc(item.name || item.key)} · ${esc(item.key || '')}</option>`).join('');
}
function pipelineReactionOptions() {
  if (!pipelineReactions.length) return '<option value="">Нет reaction assets</option>';
  return pipelineReactions.map(item => `<option value="${Number(item.id)}">${esc(item.name || `Reaction #${item.id}`)}${item.available === false ? ' · недоступно' : ''}</option>`).join('');
}
function pipelinePoolOptions() {
  if (!pipelineReactionPools.length) return '<option value="">Нет пулов реакций</option>';
  return pipelineReactionPools.map(item => `<option value="${Number(item.id)}">${esc(item.name || `Pool #${item.id}`)} · ${Number((item.items || []).length)} файлов</option>`).join('');
}
function pipelineRenderProfileOptions() {
  return (pipelineRenderProfiles || []).map(item => `<option value="${esc(item.key)}">${esc(item.label || item.key)}</option>`).join('');
}
function pipelineSelectedTemplate() {
  const id = Number(document.getElementById('pipeline-template')?.value || 0);
  return (pipelineTemplates || []).find(item => Number(item.id) === id) || null;
}
function pipelineRendererOptions(template = pipelineSelectedTemplate()) {
  const renderers = template?.supported_renderers || template?.definition?.supported_renderers || ['ffmpeg_fast'];
  const selected = document.getElementById('pipeline-renderer')?.value || template?.default_renderer || template?.definition?.default_renderer || renderers[0] || 'ffmpeg_fast';
  return renderers.map(renderer => `<option value="${esc(renderer)}"${renderer === selected ? ' selected' : ''}>${renderer === 'ffmpeg_fast' ? 'FFmpeg Fast' : 'Remotion'}</option>`).join('');
}
function studioParameterLabel(key) {
  return ({
    reaction_position: 'Позиция реакции',
    reaction_height: 'Высота реакции',
    pip_position: 'PIP позиция',
    main_fit: 'Основное видео',
    reaction_fit: 'Reaction fit',
    background_color: 'Фон',
    main_volume: 'Громкость видео',
    reaction_volume: 'Громкость реакции',
    mute_reaction: 'Заглушить реакцию',
    top_text: 'Верхний текст',
    bottom_text: 'Нижний текст',
  })[key] || key;
}
function renderStudioParameterControls(params, attrName, cssClass = '') {
  const entries = Object.entries(params || {});
  if (!entries.length) return '<div class="mono dim">У шаблона нет дополнительных параметров.</div>';
  return entries.map(([key, meta]) => {
    const type = meta?.type || 'text';
    const value = meta?.default ?? '';
    const attrs = `${attrName}="${esc(key)}"`;
    if (type === 'select') {
      const options = (meta.values || []).map(item => `<option value="${esc(item)}"${String(item) === String(value) ? ' selected' : ''}>${esc(item)}</option>`).join('');
      return `<label class="mini-field ${cssClass}"><span class="field-lbl">${esc(studioParameterLabel(key))}</span><select ${attrs} onchange="renderPipelineReactionMode()">${options}</select></label>`;
    }
    if (type === 'boolean') {
      return `<label class="toggle-label ${cssClass}"><input type="checkbox" ${attrs} ${value ? 'checked' : ''} onchange="renderPipelineReactionMode()"> ${esc(studioParameterLabel(key))}</label>`;
    }
    if (type === 'color') {
      return `<label class="mini-field ${cssClass}"><span class="field-lbl">${esc(studioParameterLabel(key))}</span><input type="color" ${attrs} value="${esc(value || '#000000')}"></label>`;
    }
    if (type === 'number') {
      const min = meta.min !== undefined ? ` min="${esc(meta.min)}"` : '';
      const max = meta.max !== undefined ? ` max="${esc(meta.max)}"` : '';
      return `<label class="mini-field ${cssClass}"><span class="field-lbl">${esc(studioParameterLabel(key))}</span><input type="number" step="any"${min}${max} ${attrs} value="${esc(value)}" onchange="renderPipelineReactionMode()"></label>`;
    }
    return `<label class="mini-field ${cssClass}"><span class="field-lbl">${esc(studioParameterLabel(key))}</span><input type="text" maxlength="${Number(meta.max_length || 200)}" ${attrs} value="${esc(value)}"></label>`;
  }).join('');
}
function collectStudioParameterValues(selector) {
  const values = {};
  document.querySelectorAll(selector).forEach(field => {
    const key = field.getAttribute('data-pipeline-template-param') || field.getAttribute('data-workspace-template-param');
    if (!key) return;
    if (field.type === 'checkbox') values[key] = Boolean(field.checked);
    else if (field.type === 'number') values[key] = field.value === '' ? null : Number(field.value);
    else values[key] = field.value;
  });
  return values;
}
function renderPipelineTemplateParams() {
  const el = document.getElementById('pipeline-template-params');
  const renderer = document.getElementById('pipeline-renderer');
  const template = pipelineSelectedTemplate();
  if (renderer) renderer.innerHTML = pipelineRendererOptions(template);
  if (!el) return;
  const params = template?.parameters || template?.definition?.parameters || {};
  el.innerHTML = `<details class="workspace-editing-param-details" open><summary>Параметры шаблона · ${esc(template?.name || '—')}</summary><div class="workspace-editing-param-grid">${renderStudioParameterControls(params, 'data-pipeline-template-param', 'pipeline-param-field')}</div></details>`;
}
function pipelineTagOptions(kind) {
  const tags = (catalogTags || []).filter(tag => tag.enabled !== false && tag.kind === kind);
  return tags.map(tag => `<option value="${Number(tag.id)}">${esc(tag.name)} · ${esc(tag.slug || '')}</option>`).join('');
}
function renderPipelineForm() {
  const el = document.getElementById('pipeline-form');
  if (!el) return;
  el.innerHTML = `<div class="pipeline-form">
    <section class="pipeline-step">
      <div class="pipeline-step-num">1</div>
      <div class="pipeline-step-body">
        <h3>Источник</h3>
        <div class="g2 compact-grid">
          <div class="field">
            <label class="field-lbl">Режим источника</label>
            <select id="pipeline-source-mode" onchange="renderPipelineSourceMode()">
              <option value="workspace">Выбрать из workspace sources/</option>
              <option value="external_file">Импортировать внешний файл</option>
            </select>
          </div>
          <div class="field" id="pipeline-external-source-field" style="display:none">
            <label class="field-lbl">Внешний video-файл</label>
            <div class="path-pick-row">
              <input id="pipeline-source-path" type="text" placeholder="/home/user/video.mp4">
              <button class="btn-secondary" id="pipeline-source-pick-btn" onclick="pickPipelineExternalFile()">Выбрать…</button>
            </div>
          </div>
        </div>
        <div class="field" id="pipeline-workspace-source-field">
          <label class="field-lbl">Видео из sources/</label>
          <select id="pipeline-source-paths" multiple size="7">${pipelineSourceOptions()}</select>
          <div class="mono dim">Можно выбрать несколько исходников. Если файла нет — импортируйте его через внешний файл или вкладку «Файлы».</div>
        </div>
      </div>
    </section>
    <section class="pipeline-step">
      <div class="pipeline-step-num">2</div>
      <div class="pipeline-step-body">
        <h3>Нарезка</h3>
        <div class="g2 compact-grid">
          <div class="field"><label class="field-lbl">Длина сегмента, сек</label><input id="pipeline-split-seconds" type="number" min="1" value="60"></div>
          <div class="field"><label class="field-lbl">Диапазоны пропуска</label><input id="pipeline-skip" type="text" placeholder="00:01:30-00:05:00, start-00:00:10"></div>
        </div>
        <label class="toggle-label"><input id="pipeline-overwrite" type="checkbox"> Перезаписать существующую нарезку этого запуска</label>
      </div>
    </section>
    <section class="pipeline-step">
      <div class="pipeline-step-num">3</div>
      <div class="pipeline-step-body">
        <h3>Studio/Remotion шаблон</h3>
        <div class="g2 compact-grid">
          <div class="field"><label class="field-lbl">Шаблон</label><select id="pipeline-template" onchange="renderPipelineTemplateParams();renderPipelineReactionMode()">${pipelineTemplateOptions()}</select></div>
          <div class="field"><label class="field-lbl">Renderer</label><select id="pipeline-renderer" onchange="refreshPipelineHealth()">${pipelineRendererOptions()}</select></div>
          <div class="field"><label class="field-lbl">Render profile</label><select id="pipeline-render-profile">${pipelineRenderProfileOptions()}</select></div>
          <div class="field"><label class="field-lbl">Reaction strategy</label><select id="pipeline-reaction-strategy" onchange="renderPipelineReactionMode()"><option value="fixed_asset">Конкретная реакция</option><option value="pool_first">Первый файл из пула</option><option value="pool_weighted">Случайно по весам из пула</option></select></div>
          <div class="field pipeline-reaction-asset-field"><label class="field-lbl">Reaction asset</label><select id="pipeline-reaction-asset">${pipelineReactionOptions()}</select></div>
          <div class="field pipeline-reaction-pool-field" style="display:none"><label class="field-lbl">Reaction pool</label><select id="pipeline-reaction-pool">${pipelinePoolOptions()}</select></div>
          <div class="field"><label class="field-lbl">Ограничение render, сек</label><input id="pipeline-duration-limit" type="number" min="1" placeholder="из render profile"></div>
        </div>
        <div id="pipeline-template-params" class="workspace-editing-params"></div>
        <label class="toggle-label"><input id="pipeline-full-length" type="checkbox"> Рендерить полную длину сегмента</label>
      </div>
    </section>
    <section class="pipeline-step">
      <div class="pipeline-step-num">4</div>
      <div class="pipeline-step-body">
        <h3>Теги и профиль</h3>
        <div class="g2 compact-grid">
          <div class="field"><label class="field-lbl">Глобальные теги</label><select id="pipeline-tags" multiple size="6">${pipelineTagOptions('user')}</select></div>
          <div class="field"><label class="field-lbl">Channel-тег</label><select id="pipeline-channel-tag"><option value="">Без автодобавления в профиль</option>${pipelineTagOptions('channel')}</select><div class="mono dim">Если выбран channel-тег, готовые видео попадут в профили через правила тегов.</div></div>
        </div>
      </div>
    </section>
    <div class="action-row pipeline-actions">
      <button class="btn-secondary" onclick="planShortsPipeline()">План</button>
      <button class="btn-primary" onclick="runShortsPipeline()">Запустить цикл</button>
    </div>
  </div>`;
  renderPipelineSourceMode();
  renderPipelineTemplateParams();
  renderPipelineReactionMode();
}
function renderPipelineSourceMode() {
  const mode = document.getElementById('pipeline-source-mode')?.value || 'workspace';
  const external = document.getElementById('pipeline-external-source-field');
  const workspace = document.getElementById('pipeline-workspace-source-field');
  if (external) external.style.display = mode === 'external_file' ? '' : 'none';
  if (workspace) workspace.style.display = mode === 'workspace' ? '' : 'none';
}
function renderPipelineReactionMode() {
  const template = pipelineSelectedTemplate();
  const params = collectStudioParameterValues('[data-pipeline-template-param]');
  const reactionSlot = template?.definition?.slots?.reaction || null;
  const needsReaction = Boolean(reactionSlot) && params.reaction_position !== 'none';
  const strategy = document.getElementById('pipeline-reaction-strategy')?.value || 'fixed_asset';
  document.querySelectorAll('.pipeline-reaction-asset-field').forEach(el => el.style.display = needsReaction && strategy === 'fixed_asset' ? '' : 'none');
  document.querySelectorAll('.pipeline-reaction-pool-field').forEach(el => el.style.display = needsReaction && strategy !== 'fixed_asset' ? '' : 'none');
  const strategyField = document.getElementById('pipeline-reaction-strategy')?.closest('.field');
  if (strategyField) strategyField.style.display = needsReaction ? '' : 'none';
}
async function loadPipelineView(options = {}) {
  const {silent = false} = options;
  if (!silent) hideInlineError('pipeline-error');
  try {
    const [sourcesData, templatesData, reactionsData, poolsData, profilesData, _tagsData, runsData, healthData] = await Promise.all([
      api.get('/api/studio/apply/sources'),
      api.get('/api/studio/templates'),
      api.get('/api/studio/reactions'),
      api.get('/api/studio/reaction-pools'),
      api.get('/api/studio/render-profiles'),
      loadCatalogTags({force: true}).then(() => ({items: catalogTags})),
      api.get('/api/shorts-pipeline/runs'),
      api.get('/api/shorts-pipeline/health?renderer_engine=ffmpeg_fast'),
    ]);
    pipelineSources = sourcesData || {sections: []};
    pipelineTemplates = templatesData.items || [];
    pipelineReactions = reactionsData.items || [];
    pipelineReactionPools = poolsData.items || [];
    pipelineRenderProfiles = profilesData.profiles || [];
    pipelineRuns = runsData.items || [];
    pipelineHealth = healthData || null;
    renderPipelineForm();
    renderPipelineHealth('pipeline-health');
    renderPipelineRuns();
    startPipelinePollingIfNeeded();
  } catch (err) {
    if (!silent) showInlineError('pipeline-error', err.message || 'Не удалось загрузить конвейер');
  }
}
async function pickPipelineExternalFile() {
  const path = await pickLocalPath({
    kind: 'file',
    title: 'Выберите исходное видео для конвейера',
    buttonId: 'pipeline-source-pick-btn',
    errorId: 'pipeline-error',
  });
  if (path) document.getElementById('pipeline-source-path').value = path;
}
function selectedOptionsValues(id) {
  return Array.from(document.getElementById(id)?.selectedOptions || []).map(option => option.value).filter(Boolean);
}
function pipelineSkipValues() {
  const raw = document.getElementById('pipeline-skip')?.value || '';
  return raw.split(',').map(item => item.trim()).filter(Boolean);
}
function pipelineRequestBody() {
  const sourceMode = document.getElementById('pipeline-source-mode')?.value || 'workspace';
  const strategy = document.getElementById('pipeline-reaction-strategy')?.value || 'fixed_asset';
  const durationText = document.getElementById('pipeline-duration-limit')?.value || '';
  return {
    source_mode: sourceMode,
    source_path: sourceMode === 'external_file' ? (document.getElementById('pipeline-source-path')?.value || '') : null,
    source_paths: sourceMode === 'workspace' ? selectedOptionsValues('pipeline-source-paths') : [],
    split_seconds: Number(document.getElementById('pipeline-split-seconds')?.value || 60),
    skip: pipelineSkipValues(),
    overwrite: Boolean(document.getElementById('pipeline-overwrite')?.checked),
    studio_template_id: Number(document.getElementById('pipeline-template')?.value || 0),
    reaction_strategy: strategy,
    reaction_asset_id: strategy === 'fixed_asset' ? Number(document.getElementById('pipeline-reaction-asset')?.value || 0) || null : null,
    reaction_pool_id: strategy === 'fixed_asset' ? null : Number(document.getElementById('pipeline-reaction-pool')?.value || 0) || null,
    parameter_values: collectStudioParameterValues('[data-pipeline-template-param]'),
    renderer_engine: document.getElementById('pipeline-renderer')?.value || 'ffmpeg_fast',
    render_profile: document.getElementById('pipeline-render-profile')?.value || 'low_540p',
    duration_limit_sec: durationText ? Number(durationText) : null,
    full_length: Boolean(document.getElementById('pipeline-full-length')?.checked),
    tag_ids: selectedOptionsValues('pipeline-tags').map(Number),
    channel_tag_id: Number(document.getElementById('pipeline-channel-tag')?.value || 0) || null,
  };
}
function renderPipelinePlan(data) {
  const el = document.getElementById('pipeline-plan');
  if (!el) return;
  const plan = data?.plan || {};
  const sources = plan.sources || [];
  el.innerHTML = `<div class="pipeline-plan-card">
    <div class="result-ok" style="display:flex"><i class="ti ti-circle-check"></i><div><div class="t">План готов</div><div class="s">${Number(plan.source_count || 0)} исходников · ${Number(plan.segments_count || 0)} сегментов · ${esc(plan.template?.name || 'template')}</div></div></div>
    <div class="pipeline-plan-list">${sources.map(source => `<div class="pipeline-plan-row"><b>${esc(source.workspace_path || shortPath(source.source_path || ''))}</b><span>${Number(source.segments_count || 0)} сегментов · ${esc(formatDurationSec(source.duration_sec))}</span></div>`).join('')}</div>
    <div class="mono dim">${plan.will_sync_profiles ? 'После render будет запущено добавление в профили по channel-тегу.' : 'Channel-тег не выбран: автодобавления в профиль не будет.'}</div>
  </div>`;
}
async function planShortsPipeline() {
  hideInlineError('pipeline-error');
  try {
    const data = await api.post('/api/shorts-pipeline/plan', pipelineRequestBody());
    renderPipelinePlan(data);
  } catch (err) {
    showInlineError('pipeline-error', err.message || 'Не удалось построить план');
  }
}
async function runShortsPipeline() {
  hideInlineError('pipeline-error');
  try {
    const body = pipelineRequestBody();
    await api.post('/api/shorts-pipeline/preflight', body);
    const data = await api.post('/api/shorts-pipeline/runs', body);
    const run = data.run;
    pipelineActiveRunId = run?.id || null;
    pipelineRuns = [run, ...pipelineRuns.filter(item => Number(item.id) !== Number(run.id))];
    await refreshPipelineHealth({silent: true});
    renderPipelineRuns();
    startPipelinePollingIfNeeded(true);
    showToast(`Конвейер запущен #${run.id}`);
  } catch (err) {
    showInlineError('pipeline-error', err.message || 'Не удалось запустить конвейер');
  }
}
function pipelineHealthClass(level) {
  if (level === 'error') return 'is-error';
  if (level === 'warn') return 'is-warn';
  if (level === 'ok') return 'is-ok';
  return 'is-info';
}
function pipelineHealthCheckIcon(ok) {
  return ok ? '<i class="ti ti-circle-check"></i>' : '<i class="ti ti-alert-triangle"></i>';
}
function pipelineHealthActionButtons(run) {
  if (!run || !['queued', 'splitting', 'rendering', 'syncing_profile'].includes(run.status)) return '';
  const batchProgress = run.batch?.progress || {};
  const failed = Number(batchProgress.failed || 0);
  return `<div class="pipeline-health-actions">
    <button class="btn-mini" onclick="continueShortsPipelineRun(${Number(run.id)})">Продолжить очередь</button>
    <button class="btn-mini" onclick="repairShortsPipelineRun(${Number(run.id)})">Починить зависший запуск</button>
    ${failed ? `<button class="btn-mini" onclick="retryFailedShortsPipelineRun(${Number(run.id)})">Повторить failed (${failed})</button>` : ''}
    <button class="btn-mini" onclick="finishShortsPipelineWithErrors(${Number(run.id)})">Завершить с ошибками</button>
  </div>`;
}
function renderPipelineHealth(targetId = 'pipeline-health') {
  const el = document.getElementById(targetId);
  if (!el) return;
  const health = pipelineHealth || {};
  const queue = health.queue || {};
  const preflight = health.preflight || {checks: []};
  const notes = health.notes || [];
  const run = health.run || null;
  const checks = preflight.checks || [];
  if (!health.queue && !run && !checks.length) {
    el.innerHTML = '<div class="empty compact">Диагностика конвейера пока не загружена.</div>';
    return;
  }
  const batchProgress = run?.batch?.progress || {};
  const queueStatus = queue.status || 'idle';
  el.innerHTML = `<div class="pipeline-health-card ${health.ok ? 'is-ok' : 'is-warn'}">
    <div class="pipeline-health-head">
      <div>
        <div class="field-lbl">Здоровье конвейера</div>
        <div class="mono txt">${run ? `Запуск #${Number(run.id)} · ${esc(pipelineRunStageText(run))}` : 'Активного запуска нет'}</div>
      </div>
      <span class="pill ${health.ok ? 'ok' : 'warn'}">${health.ok ? 'OK' : 'Проверить'}</span>
    </div>
    <div class="pipeline-health-metrics">
      <div><b>${esc(queueStatus)}</b><span>render queue</span></div>
      <div><b>${Number(queue.queued_count || 0)}</b><span>queued</span></div>
      <div><b>${Number(queue.rendering_count || 0)}</b><span>rendering</span></div>
      <div><b>${Number(queue.failed_count || 0)}</b><span>failed всего</span></div>
      <div><b>${Number(batchProgress.done || 0)}/${Number(batchProgress.total || 0)}</b><span>batch done</span></div>
    </div>
    ${notes.length ? `<div class="pipeline-health-notes">${notes.map(note => `<div class="pipeline-health-note ${pipelineHealthClass(note.level)}">${esc(note.message || '')}</div>`).join('')}</div>` : ''}
    <details class="pipeline-preflight">
      <summary>Preflight проверки</summary>
      <div class="pipeline-check-grid">${checks.map(item => `<div class="pipeline-check ${item.ok ? 'is-ok' : 'is-error'}">${pipelineHealthCheckIcon(item.ok)}<div><b>${esc(item.label || item.key)}</b><span>${esc(item.message || '')}</span>${item.value ? `<small class="mono dim">${esc(item.value)}</small>` : ''}</div></div>`).join('')}</div>
    </details>
    ${pipelineHealthActionButtons(run)}
  </div>`;
}
async function refreshPipelineHealth(options = {}) {
  const {silent = false} = options;
  try {
    const renderer = document.getElementById('pipeline-renderer')?.value || 'ffmpeg_fast';
    pipelineHealth = await api.get(`/api/shorts-pipeline/health?renderer_engine=${encodeURIComponent(renderer)}`);
    renderPipelineHealth('pipeline-health');
    renderPipelineHealth('queue-pipeline-health');
    if (pipelineHealth?.run) {
      const run = pipelineHealth.run;
      pipelineRuns = [run, ...pipelineRuns.filter(item => Number(item.id) !== Number(run.id))];
    }
    return pipelineHealth;
  } catch (err) {
    if (!silent) showToast(err.message || 'Не удалось обновить диагностику конвейера', 'err');
    return null;
  }
}
function pipelineRunProgress(run) {
  const batchProgress = run?.batch?.progress;
  if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') return 100;
  if (run.status === 'queued') return 5;
  if (run.status === 'splitting') return 20;
  if (run.status === 'rendering') {
    const renderPercent = batchProgress ? Number(batchProgress.percent || 0) : 0;
    return Math.min(94, Math.round(25 + renderPercent * 0.65));
  }
  if (run.status === 'syncing_profile') return 95;
  return 5;
}
function pipelineRunStageText(run) {
  const progress = run?.batch?.progress;
  const summary = run?.summary || {};
  if (run.status === 'queued') return 'Ожидает запуска';
  if (run.status === 'splitting') return 'Нарезка исходников';
  if (run.status === 'rendering') return progress?.message || 'Studio render';
  if (run.status === 'syncing_profile') return 'Синхронизация с профилями по тегам';
  if (run.status === 'done' && (Number(summary.failed || 0) > 0 || run.error)) return 'Готово с ошибками';
  if (run.status === 'done') return 'Готово';
  if (run.status === 'failed') return 'Ошибка';
  if (run.status === 'cancelled') return 'Отменено';
  return ruStatus(run.status);
}
function pipelineRunCard(run) {
  const progress = pipelineRunProgress(run);
  const active = ['queued', 'splitting', 'rendering', 'syncing_profile'].includes(run.status);
  const summary = run.summary || {};
  const outputs = (run.items || []).filter(item => item.output_workspace_path);
  return `<article class="pipeline-run-card">
    <div class="pipeline-run-head">
      <div><b>Конвейер #${Number(run.id)}</b><div class="mono dim">${esc(run.template_key || '')} · ${esc(run.render_profile || '')}</div></div>
      ${badge(run.status)}
    </div>
    <progress value="${progress}" max="100"></progress>
    <div class="pbar-row"><span class="mono dim">${esc(pipelineRunStageText(run))}</span><span class="mono dim">${progress}%</span></div>
    <div class="mono dim">${Number(summary.sources || 0)} исходников · ${Number(summary.segments || 0)} сегментов · ${Number(summary.render_jobs || 0)} render jobs${summary.profile_sync ? ` · профили: ${Number(summary.profile_sync.added || 0)} видео` : ''}</div>
    ${run.error ? `<div class="err-line">${esc(shortErrorText(run.error))}</div>` : ''}
    ${outputs.length ? `<div class="pipeline-output-list">${outputs.slice(0, 6).map(item => `<button class="link-video mono" data-path="${esc(item.output_workspace_path)}" onclick="openWebPlayer(this.dataset.path)">${esc(workspaceDisplayPath(item.output_workspace_path))}</button>`).join('')}</div>` : ''}
    <div class="row-actions">
      ${run.remotion_batch_id ? `<button class="btn-mini" onclick="openPipelineBatch(${Number(run.remotion_batch_id)})">Открыть batch</button>` : ''}
      ${active ? `<button class="btn-danger" onclick="cancelShortsPipelineRun(${Number(run.id)})">Отменить</button>` : ''}
    </div>
  </article>`;
}
function renderPipelineRuns() {
  const el = document.getElementById('pipeline-runs');
  if (!el) return;
  if (!pipelineRuns.length) {
    el.innerHTML = '<div class="empty compact">Запусков конвейера пока нет.</div>';
    return;
  }
  el.innerHTML = `<div class="pipeline-runs-list">${pipelineRuns.map(pipelineRunCard).join('')}</div>`;
}
async function refreshPipelineRuns() {
  if (currentView !== 'pipeline') return;
  try {
    const [data, healthData] = await Promise.all([
      api.get('/api/shorts-pipeline/runs'),
      api.get('/api/shorts-pipeline/health?renderer_engine=ffmpeg_fast'),
    ]);
    pipelineRuns = data.items || [];
    pipelineHealth = healthData || null;
    const active = pipelineRuns.find(run => ['queued', 'splitting', 'rendering', 'syncing_profile'].includes(run.status));
    pipelineActiveRunId = active?.id || null;
    renderPipelineHealth('pipeline-health');
    renderPipelineRuns();
    startPipelinePollingIfNeeded();
  } catch (err) {
    showInlineError('pipeline-error', err.message || 'Не удалось обновить конвейер');
  }
}
function startPipelinePollingIfNeeded(force = false) {
  const hasActive = pipelineRuns.some(run => ['queued', 'splitting', 'rendering', 'syncing_profile'].includes(run.status));
  if (!hasActive && !force) {
    if (pipelinePollTimer) window.clearInterval(pipelinePollTimer);
    pipelinePollTimer = null;
    return;
  }
  if (pipelinePollTimer) return;
  pipelinePollTimer = window.setInterval(() => {
    if (currentView !== 'pipeline') return;
    refreshPipelineRuns();
  }, 2000);
}
async function cancelShortsPipelineRun(runId) {
  if (!confirm(`Отменить запуск конвейера #${runId}?`)) return;
  try {
    const data = await api.post(`/api/shorts-pipeline/runs/${Number(runId)}/cancel`, {});
    pipelineRuns = [data.run, ...pipelineRuns.filter(item => Number(item.id) !== Number(runId))];
    await refreshPipelineHealth({silent: true});
    renderPipelineRuns();
    renderQueuePipelineRuns(pipelineRuns);
  } catch (err) {
    const message = err.message || 'Не удалось отменить конвейер';
    if (document.getElementById('pipeline-error')) showInlineError('pipeline-error', message);
    else showToast(message, 'err');
  }
}
async function pipelineRunOperatorAction(runId, action, label, options = {}) {
  if (options.confirmText && !confirm(options.confirmText)) return;
  try {
    const data = await api.post(`/api/shorts-pipeline/runs/${Number(runId)}/${action}`, {});
    if (data.run) {
      pipelineRuns = [data.run, ...pipelineRuns.filter(item => Number(item.id) !== Number(runId))];
    }
    if (data.health) pipelineHealth = data.health;
    await refreshPipelineHealth({silent: true});
    renderPipelineRuns();
    renderQueuePipelineRuns(pipelineRuns);
    showToast(label);
  } catch (err) {
    const message = err.message || `Не удалось выполнить действие: ${label}`;
    if (document.getElementById('pipeline-error')) showInlineError('pipeline-error', message);
    showToast(message, 'err');
  }
}
function continueShortsPipelineRun(runId) {
  pipelineRunOperatorAction(runId, 'continue', 'Очередь конвейера продолжена');
}
function repairShortsPipelineRun(runId) {
  pipelineRunOperatorAction(runId, 'repair', 'Проверка и repair конвейера выполнены');
}
function retryFailedShortsPipelineRun(runId) {
  pipelineRunOperatorAction(runId, 'retry-failed', 'Failed render jobs возвращены в очередь');
}
function finishShortsPipelineWithErrors(runId) {
  pipelineRunOperatorAction(runId, 'finish-with-errors', 'Конвейер завершён с ошибками', {
    confirmText: `Завершить конвейер #${runId} с ошибками? Оставшиеся queued jobs будут отменены.`,
  });
}
function openPipelineBatch(batchId) {
  const url = new URL(window.location.href);
  url.searchParams.set('batch', String(batchId));
  window.history.replaceState({}, '', url);
  nav('studio', document.querySelector('[data-v="studio"]'));
}

function openWorkspaceSettings() {
  nav('settings', document.querySelector('[data-v="settings"]'));
  setSettingsTab('workspace', document.querySelector('[data-settings-tab="workspace"]'));
}

function activateInitialViewFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const profileId = Number(params.get('profile') || 0);
  if (profileId) {
    openStorageProfile(profileId, {replace: true});
    return;
  }
  if (params.has('batch') || params.has('project')) {
    nav('studio', document.querySelector('[data-v="studio"]'));
  }
}

function handleProfileRouteFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const profileId = Number(params.get('profile') || 0);
  if (profileId) {
    openStorageProfile(profileId, {replace: true});
  } else if (currentView === 'storage-profile') {
    openStorageProfilesHub({keepUrl: true});
  }
}

async function loadDashboard() {
  try {
    const [data, pipelineData] = await Promise.all([
      api.get('/api/status'),
      api.get('/api/shorts-pipeline/runs'),
    ]);
    const jobs = data.jobs || {};
    const clips = data.clips || {};
    const videos = data.videos_by_status || {};
    const runningJobs = jobs.running || 0;
    const queuedJobs = jobs.queued || 0;
    const activePipelineRuns = (pipelineData.items || []).filter(run => ['queued', 'splitting', 'rendering', 'syncing_profile'].includes(run.status)).length;
    const queuedClips = clips.queued || 0;
    const failedClips = clips.failed || 0;

    document.getElementById('st-videos').textContent = fmtNum(data.videos_total);
    document.getElementById('st-segments').textContent = fmtNum(data.segments_total);
    document.getElementById('st-jobs').textContent = fmtNum(runningJobs + queuedJobs + activePipelineRuns);
    document.getElementById('st-clips').textContent = fmtNum(queuedClips);
    document.getElementById('st-videos-sub').textContent = `${videos.inbox||0} входящие · ${videos.reviewed||0} просмотрено`;
    document.getElementById('st-jobs-sub').textContent = `${runningJobs} split в работе · ${queuedJobs} split в очереди · ${activePipelineRuns} конвейер`;
    document.getElementById('st-clips-sub').textContent = `${clips.done||0} готово · ${failedClips} ошибок`;
    document.getElementById('nav-jobs').textContent = runningJobs + queuedJobs + activePipelineRuns || '';
    document.getElementById('job-pulse').classList.toggle('pulse', runningJobs > 0 || activePipelineRuns > 0);

    renderStudioMigrationWarning(data.studio_migration_warning);
    renderRunningBanner(data.latest_jobs || []);
    renderJobsTable('dash-jobs', data.latest_jobs || [], false);
    renderVideoStatusBars(videos, data.videos_total || 0);
    renderErrors(data.recent_errors || []);
    lastOutputs = data.latest_outputs || lastOutputs;
  } catch (err) {
    showError('dash-jobs', err);
  }
}

function renderStudioMigrationWarning(warning) {
  const el = document.getElementById('studio-migration-warning');
  if (!el) return;
  if (!warning) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="banner" style="background:var(--warn-bg);border-color:rgba(245,158,11,.35);">
    <div class="banner-left" style="justify-content:space-between;align-items:flex-start">
      <div>
        <div class="badge b-warn" style="margin-bottom:8px">Template Studio migration</div>
        <div class="txt">${esc(warning.message || 'Проверка миграции Template Studio нашла проблемы.')}</div>
        <div class="mono mid" style="margin-top:6px">mode=${esc(warning.mode || 'unknown')}</div>
      </div>
      <button class="btn-mini" onclick="nav('settings',document.querySelector('[data-v=settings]'))">Настройки</button>
    </div>
  </div>`;
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
  const normalized = String(path || '').replace(/\/+$/, '');
  const job = lastJobs.find(item => String(item.output_dir || '').replace(/\/+$/, '') === normalized);
  if (job) {
    await openQueueClipsForJob(job.id);
    return;
  }
  const item = lastClips.find(row => (
    String(row.folder_path || '').replace(/\/+$/, '') === normalized
    || String(row.path || '').startsWith(normalized + '/')
  ));
  if (item) setWorkspaceParentVideoFilter(workspaceParentFilterFromItem(item));
  else clearWorkspaceParentVideoFilter({silent: true});
  activateView('queue', document.querySelector('[data-v="queue"]'));
  setQueueSubView('clips');
  loadJobs();
  await loadClips();
  scrollQueueClipsIntoView();
  showToast(`Показаны нарезки/клипы: ${shortPath(path)}`);
}

function setQueueSubView(mode) {
  queueSubView = mode === 'clips' ? 'clips' : 'overview';
  const overview = document.getElementById('queue-overview');
  const clips = document.getElementById('queue-clips-section');
  if (overview) overview.hidden = queueSubView !== 'overview';
  if (clips) clips.hidden = queueSubView !== 'clips';
}

function showQueueOverview() {
  setQueueSubView('overview');
  loadJobs();
  document.getElementById('v-queue')?.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function scrollQueueClipsIntoView() {
  setQueueSubView('clips');
  document.getElementById('v-queue')?.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function normalizeWorkspaceFilterPath(path) {
  return String(path || '').replace(/\/+$/, '');
}

function workspaceParentFilterFromJob(job) {
  return {
    videoId: Number(job?.video_id || 0) || null,
    sourcePath: normalizeWorkspaceFilterPath(job?.source_path || ''),
    title: job?.current_file || (job?.source_path ? shortPath(job.source_path) : `Задача #${job?.id || ''}`),
  };
}

function workspaceParentFilterFromItem(item) {
  return {
    videoId: Number(item?.video_id || 0) || null,
    sourcePath: normalizeWorkspaceFilterPath(item?.source_path || ''),
    title: item?.video_title || item?.title || item?.file_name || shortPath(item?.source_path || item?.path || ''),
  };
}

function setWorkspaceParentVideoFilter(filter) {
  workspaceParentVideoFilter = filter && (filter.videoId || filter.sourcePath)
    ? {
        videoId: Number(filter.videoId || 0) || null,
        sourcePath: normalizeWorkspaceFilterPath(filter.sourcePath || ''),
        title: filter.title || 'исходник',
      }
    : null;
  renderWorkspaceParentFilterLine();
}

function clearWorkspaceParentVideoFilter(options = {}) {
  workspaceParentVideoFilter = null;
  renderWorkspaceParentFilterLine();
  if (!options.silent) {
    renderClipCounts(workspaceCountsFromItems(workspaceItemsForParentFilter()));
    renderClipsTable(getVisibleWorkspaceItems());
    renderWorkspaceDetail();
  }
}

async function showAllQueueClips() {
  clearWorkspaceParentVideoFilter({silent: true});
  if (currentView !== 'queue') activateView('queue', document.querySelector('[data-v="queue"]'));
  setQueueSubView('clips');
  loadJobs();
  await loadClips();
  scrollQueueClipsIntoView();
}

async function openQueueClipsForJob(jobId) {
  const job = lastJobs.find(item => Number(item.id) === Number(jobId));
  if (!job) {
    showToast('Задача не найдена', 'err');
    return;
  }
  setWorkspaceParentVideoFilter(workspaceParentFilterFromJob(job));
  if (currentView !== 'queue') activateView('queue', document.querySelector('[data-v="queue"]'));
  setQueueSubView('clips');
  loadJobs();
  await loadClips();
  scrollQueueClipsIntoView();
}

async function openQueueClipsForVideo(videoId, title = '', sourcePath = '') {
  const video = lastVideos.find(item => Number(item.id) === Number(videoId));
  setWorkspaceParentVideoFilter({
    videoId: Number(videoId) || null,
    sourcePath: sourcePath || video?.source_path || '',
    title: title || video?.title || `Видео #${videoId}`,
  });
  if (currentView !== 'queue') activateView('queue', document.querySelector('[data-v="queue"]'));
  setQueueSubView('clips');
  loadJobs();
  await loadClips();
  scrollQueueClipsIntoView();
}

async function deleteAllClipsForVideo(videoId, title = '') {
  const id = Number(videoId || 0);
  if (!id) {
    showToast('Исходник не найден', 'err');
    return;
  }
  const label = title || lastVideos.find(item => Number(item.id) === id)?.title || `Видео #${id}`;
  if (!window.confirm(`Удалить все нарезки и клипы исходника «${label}»? Файлы будут удалены с диска, это нельзя отменить.`)) return;
  const removeFromProfiles = window.confirm('Также удалить связанные видео из локальных профилей?');
  try {
    const data = await api.post(`/api/videos/${id}/clips/delete`, {remove_from_profiles: removeFromProfiles});
    await refreshWorkspaceFromDeleteResponse(data);
    const summary = data.summary || {};
    const profilePart = removeFromProfiles ? ` · из профилей: ${summary.profile_items_removed || 0}` : '';
    showToast(`Клипы исходника удалены: ${summary.hidden || 0} · файлов: ${summary.deleted_files || 0} · ошибок: ${summary.errors || 0}${profilePart}`);
    await Promise.allSettled([loadDashboard(), loadJobs()]);
  } catch (err) {
    showToast(err.message || 'Не удалось удалить клипы исходника', 'err');
  }
}

async function restoreVideoSource(videoId) {
  const id = Number(videoId || 0);
  if (!id) return;
  try {
    await api.post(`/api/videos/${id}/restore`, {});
    showToast('Исходник восстановлен в очереди');
    await Promise.allSettled([loadJobs(), loadDashboard()]);
  } catch (err) {
    showToast(err.message || 'Не удалось восстановить исходник', 'err');
  }
}

async function relinkVideoSource(videoId) {
  const id = Number(videoId || 0);
  if (!id) return;
  const path = await pickLocalPath({
    kind: 'file',
    title: 'Укажите новый путь к исходному видео',
  });
  if (!path) return;
  try {
    await api.post(`/api/videos/${id}/relink-source`, {source_path: path});
    showToast('Новый путь к исходнику сохранён');
    await Promise.allSettled([loadJobs(), loadDashboard()]);
  } catch (err) {
    showToast(err.message || 'Не удалось указать новый путь', 'err');
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

async function pickSplitPath() {
  hideInlineError('split-error');
  const isFolder = splitMode === 'folder';
  const path = await pickLocalPath({
    kind: isFolder ? 'directory' : 'file',
    title: isFolder ? 'Выберите папку для нарезки' : 'Выберите видео для нарезки',
    buttonId: 'split-path-pick-btn',
    errorId: 'split-error',
  });
  if (!path) return;
  const input = document.getElementById('split-path');
  if (input) input.value = path;
  if (isFolder) {
    await openFolder(path, {silent: true});
    manualPathChanged();
    return;
  }
  await selectVideo(path);
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
    el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Ручной путь</div><div class="selection-name" title="${esc(manual)}">${esc(shortPath(manual))}</div><div class="selection-meta mono">Резервный ручной ввод без проверки файловым браузером</div></div>`;
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
    const params = new URLSearchParams();
    if (queueKindFilter !== 'all') params.set('kind', queueKindFilter);
    if (queueStatusFilter !== 'all') params.set('status', queueStatusFilter);
    if (queueReviewFilter !== 'all') params.set('review_status', queueReviewFilter);
    if (queueSourceStateFilter !== 'all') params.set('source_state', queueSourceStateFilter);
    if (queueIncludeDeleted) params.set('include_deleted', 'true');
    if (queueSearchQuery) params.set('q', queueSearchQuery);
    const [data, queueData, pipelineData, healthData] = await Promise.all([
      api.get('/api/jobs'),
      api.get(`/api/queue/items${params.toString() ? `?${params.toString()}` : ''}`),
      api.get('/api/shorts-pipeline/runs'),
      api.get('/api/shorts-pipeline/health?renderer_engine=ffmpeg_fast'),
    ]);
    lastJobs = data.jobs || [];
    lastQueueItems = queueData.items || [];
    editingJobs = lastQueueItems.filter(item => item.kind === 'render');
    lastVideos = lastQueueItems
      .filter(item => item.kind === 'source')
      .map(queueVideoFromItem);
    selectedVideoIds = new Set(Array.from(selectedVideoIds).filter(id => lastVideos.some(video => Number(video.id) === Number(id))));
    pipelineRuns = pipelineData.items || [];
    pipelineHealth = healthData || null;
    renderPipelineHealth('queue-pipeline-health');
    renderQueuePipelineRuns(pipelineRuns);
    renderQueueCounts(queueData.counts || {});
    renderQueueItems('jobs-table', lastQueueItems);
    renderQueueSourceBulkToolbar();
  } catch (err) {
    showError('jobs-table', err);
  }
}
function renderQueuePipelineRuns(runs) {
  const el = document.getElementById('queue-pipeline-runs');
  if (!el) return;
  const items = runs || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty compact">Запусков конвейера пока нет. Запустите цикл в разделе «Конвейер».</div>';
    return;
  }
  el.innerHTML = `<div class="pipeline-runs-list">${items.slice(0, 8).map(pipelineRunCard).join('')}</div>`;
}
function renderJobCounts(counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  for (const key of ['all','queued','running','done','failed']) {
    const el = document.getElementById('jobs-cnt-' + key);
    if (el) el.textContent = key === 'all' ? total : (counts[key] || '');
  }
}
function renderQueueCounts(counts) {
  for (const key of ['all','source','jobs','split','prepare','render','review','publish','errors','missing','deleted']) {
    const el = document.getElementById('queue-cnt-' + key);
    if (el) el.textContent = counts[key] || '';
  }
}
function queueFilterConfig(name) {
  const key = String(name || 'all');
  if (key === 'source') return {kind: 'source', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'jobs') return {kind: 'jobs', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'split') return {kind: 'split', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'prepare') return {kind: 'jobs', status: 'preparing', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'render') return {kind: 'render', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'review') return {kind: 'render', status: 'done', review: 'pending', sourceState: 'all', includeDeleted: false};
  if (key === 'publish') return {kind: 'publish', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'errors') return {kind: 'all', status: 'failed', review: 'all', sourceState: 'all', includeDeleted: false};
  if (key === 'missing') return {kind: 'source', status: 'all', review: 'all', sourceState: 'missing_or_moved', includeDeleted: false};
  if (key === 'deleted') return {kind: 'source', status: 'all', review: 'all', sourceState: 'hidden_deleted', includeDeleted: true};
  return {kind: 'all', status: 'all', review: 'all', sourceState: 'all', includeDeleted: false};
}
function setQueueQuickFilter(name) {
  queueQuickFilter = String(name || 'all');
  const config = queueFilterConfig(queueQuickFilter);
  queueKindFilter = config.kind;
  queueStatusFilter = config.status;
  queueReviewFilter = config.review;
  queueSourceStateFilter = config.sourceState;
  queueIncludeDeleted = config.includeDeleted;
  document.querySelectorAll('[data-queue-filter]').forEach(btn => {
    btn.classList.toggle('on', btn.dataset.queueFilter === queueQuickFilter);
  });
  loadJobs();
}
function openQueueSources() {
  activateView('queue', document.querySelector('[data-v="queue"]'));
  setQueueSubView('overview');
  setQueueQuickFilter('source');
}
function setQueueViewMode(mode) {
  uiState.queueViewMode = mode === 'grid' ? 'grid' : 'table';
  writeUiPreference('queueViewMode', uiState.queueViewMode);
  setSegmentedState('[data-queue-view]', uiState.queueViewMode, 'data-queue-view');
  renderQueueItems('jobs-table', lastQueueItems);
}
function onQueueSearchInput(value) {
  queueSearchQuery = String(value || '');
  loadJobs();
}
function filterJobs(tab, status) {
  queueQuickFilter = status === 'failed' ? 'errors' : 'jobs';
  queueKindFilter = 'jobs';
  queueStatusFilter = status || 'all';
  queueReviewFilter = 'all';
  queueSourceStateFilter = 'all';
  queueIncludeDeleted = false;
  if (tab) {
    tab.closest('.tabs')?.querySelectorAll('.tab').forEach(item => item.classList.remove('on'));
    tab.classList.add('on');
  }
  loadJobs();
}
function queueVideoFromItem(item) {
  return {
    id: Number(item.video_id),
    title: item.title || `Видео #${item.video_id}`,
    source_path: item.source_path || '',
    duration_sec: item.duration_sec,
    duration_text: item.duration_text || '—',
    review_status: item.status || 'inbox',
    mark_count: Number(item.counts?.marks || 0),
    clip_count: Number(item.counts?.clips || 0),
    segment_count: Number(item.counts?.segments || 0),
    output_dir: item.output_dir || '',
    source_file_exists: Boolean(item.source_file_exists),
    source_state: item.source_state || 'ok',
    source_state_label: item.source_state_label || '',
    source_missing: Boolean(item.source_missing),
    source_deleted: Boolean(item.source_deleted),
    source_hidden: Boolean(item.source_hidden),
  };
}
function queueItemKindBadge(item) {
  const cls = item.kind === 'source' ? 'b-inf' : item.kind === 'publish' ? 'b-ok' : item.kind === 'render' ? 'b-warn' : 'b-dim';
  return `<span class="badge ${cls}">${esc(item.kind_label || item.kind)}</span>`;
}
function queueSourceStateBadge(item) {
  if (!item.source_state || item.source_state === 'ok') return '<span class="badge b-ok">Файл на месте</span>';
  if (item.source_state === 'hidden_deleted') return '<span class="badge b-dim">Удалённые/скрытые</span>';
  if (item.source_state === 'source_deleted') return '<span class="badge b-warn">Исходник удалён</span>';
  return '<span class="badge b-err">Missing/перемещён</span>';
}
function queueProgressBar(item) {
  const value = Math.max(0, Math.min(100, Number(item.progress || 0)));
  const cls = item.status === 'failed' ? 'pf-err' : value >= 100 ? 'pf-ok' : 'pf-info';
  return `<div class="pbar-row"><span class="mono dim">${value}%</span></div><div class="pbar"><div class="pf ${cls}" style="width:${value}%"></div></div>`;
}
function openStudioTemplate(templateId) {
  const id = Number(templateId || 0);
  if (!id) {
    showToast('Studio template не найден', 'err');
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.set('template', String(id));
  url.searchParams.delete('batch');
  url.searchParams.delete('project');
  window.history.replaceState({}, '', url);
  nav('studio', document.querySelector('[data-v="studio"]'));
}
async function openQueueLinkedProfile(youtubeAccountId, channelProfileName = '') {
  try {
    if (!storageProfiles.length) {
      const data = await api.get('/api/storage-profiles');
      storageProfiles = data.items || [];
    }
    const accountId = Number(youtubeAccountId || 0);
    const name = String(channelProfileName || '').trim().toLowerCase();
    const profile = storageProfiles.find(item =>
      accountId
      && (item.service_links || []).some(link =>
        link.platform === 'youtube'
        && Number(link.external_account_id || 0) === accountId
      )
    ) || storageProfiles.find(item =>
      name && String(item.effective_name || item.name || '').trim().toLowerCase() === name
    );
    if (profile) {
      await openStorageProfile(Number(profile.id));
      return;
    }
    nav('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
    showToast('Локальный профиль для этой задачи не найден. Открыл раздел «Профили».');
  } catch (err) {
    showToast(err.message || 'Не удалось открыть профиль', 'err');
  }
}
function queueItemActions(item) {
  const actions = [];
  if (item.kind === 'source') {
    const videoId = Number(item.video_id);
    if (item.source_file_exists) actions.push(webPlayerButton(item.source_path, 'Смотреть'));
    actions.push(`<button class="btn-mini" onclick="event.stopPropagation();openQueueClipsForVideo(${videoId})">Показать клипы</button>`);
    if (item.output_dir) actions.push(outputFolderButton(item.output_dir, 'Output'));
    if (item.source_missing || item.source_deleted) actions.push(`<button class="btn-mini" onclick="event.stopPropagation();relinkVideoSource(${videoId})">Указать новый путь…</button>`);
    if (item.source_hidden) actions.push(`<button class="btn-secondary" onclick="event.stopPropagation();restoreVideoSource(${videoId})">Восстановить</button>`);
    actions.push(`<button class="btn-danger" onclick="event.stopPropagation();deleteAllClipsForVideo(${videoId}, this.dataset.title)" data-title="${esc(item.title || '')}">Удалить все клипы</button>`);
    actions.push(`<button class="btn-danger" onclick="event.stopPropagation();openVideoDeleteDialog([${videoId}])">Удалить родительское видео</button>`);
  } else if (item.kind === 'render') {
    const jobId = Number(item.job_id);
    const finalPath = item.edited_path || item.output_path;
    if (item.status === 'done') {
      actions.push(`<button class="btn-mini" onclick="event.stopPropagation();toggleEditingJobPreview(${jobId})">${editingPreviewJobId === jobId ? 'Скрыть preview' : 'Preview'}</button>`);
      if (finalPath) actions.push(webPlayerButton(finalPath, 'Смотреть'));
      actions.push(`<button class="btn-primary" onclick="event.stopPropagation();setEditingJobReview(${jobId}, 'approved')">Одобрить</button>`);
      actions.push(`<button class="btn-danger" onclick="event.stopPropagation();setEditingJobReview(${jobId}, 'rejected')">Отклонить</button>`);
    }
    if (item.status === 'queued') actions.push(`<button class="btn-mini" onclick="event.stopPropagation();renderEditingJob(${jobId})">Рендер</button>`);
    if (['failed','cancelled'].includes(item.status)) actions.push(`<button class="btn-mini" onclick="event.stopPropagation();renderEditingJob(${jobId}, true)">Рендер заново</button>`);
    if (['queued','failed'].includes(item.status)) actions.push(`<button class="btn-danger" onclick="event.stopPropagation();cancelEditingJob(${jobId})">Отменить</button>`);
    if (['failed','cancelled'].includes(item.status)) actions.push(`<button class="btn-secondary" onclick="event.stopPropagation();retryEditingJob(${jobId})">Повторить</button>`);
    if (item.output_dir) actions.push(outputFolderButton(item.output_dir, 'Папка'));
    if (item.studio_template_id) actions.push(`<button class="btn-mini" onclick="event.stopPropagation();openStudioTemplate(${Number(item.studio_template_id)})">Template</button>`);
    if (item.channel_profile_id || item.youtube_account_id) actions.push(`<button class="btn-mini" data-account="${esc(item.youtube_account_id || '')}" data-name="${esc(item.channel_profile_name || '')}" onclick="event.stopPropagation();openQueueLinkedProfile(this.dataset.account, this.dataset.name)">Профиль</button>`);
  } else {
    if (item.video_id) actions.push(`<button class="btn-mini" onclick="event.stopPropagation();openQueueClipsForVideo(${Number(item.video_id)})">Клипы</button>`);
    if (item.output_dir) actions.push(outputFolderButton(item.output_dir, 'Папка'));
    if (item.source_path && item.source_file_exists) actions.push(mpvButton(item.source_path));
  }
  return `<div class="row-actions">${actions.join('')}</div>`;
}
function toggleQueueItemExpanded(itemId) {
  if (expandedQueueItemIds.has(itemId)) expandedQueueItemIds.delete(itemId);
  else expandedQueueItemIds.add(itemId);
  renderQueueItems('jobs-table', lastQueueItems);
}
function queueItemDetails(item) {
  if (item.kind === 'render') {
    const jobId = Number(item.job_id);
    const review = item.review_status || 'pending';
    const finalPath = item.edited_path || item.output_path || '';
    const previewOpen = item.status === 'done' && editingPreviewJobId === jobId;
    const preview = previewOpen
      ? `<div class="editing-review-panel">
          <div class="editing-video-wrap"><video controls preload="metadata" src="/api/editing/jobs/${jobId}/media"></video></div>
          <div class="editing-review-controls">
            <div class="selection-title">Проверка результата</div>
            <div class="mono dim">Рендер: ${badge(item.status)} · Проверка: ${badge(review)}</div>
            <label class="field"><span class="field-lbl">Комментарий</span><textarea id="editing-review-note-${jobId}" rows="5" placeholder="Почему одобрено или что нужно переделать">${esc(item.review_note || '')}</textarea></label>
            <div class="row-actions">
              <button class="btn-primary" onclick="setEditingJobReview(${jobId}, 'approved')">Одобрить</button>
              <button class="btn-danger" onclick="setEditingJobReview(${jobId}, 'rejected')">Отклонить</button>
              ${review !== 'pending' ? `<button class="btn-secondary" onclick="resetEditingJobReview(${jobId})">Сбросить проверку</button>` : ''}
            </div>
          </div>
        </div>`
      : '';
    return `<div class="queue-item-details">
      <div class="inspector-kv compact">
        <div><b>Edit job</b><span class="mono">#${jobId}</span></div>
        <div><b>Workspace</b><span class="mono">${esc(item.workspace_item_key || '—')}</span></div>
        <div><b>Профиль</b><span>${esc(item.channel_profile_name || `#${item.channel_profile_id || '—'}`)}</span></div>
        <div><b>Template</b><span>${esc(item.template_name || item.template_key || '—')}</span></div>
        <div><b>Reaction</b><span>${esc(item.reaction_asset_name || 'без реакции / pool')}</span></div>
        <div><b>Renderer</b><span class="mono">${esc(item.renderer || '—')}</span></div>
        <div><b>Review</b><span>${badge(review)}</span></div>
      </div>
      ${finalPath ? `<div class="mono dim">Результат: ${esc(finalPath)}</div>` : ''}
      ${item.error ? `<div class="err">Ошибка: ${esc(shortErrorText(item.error))}</div>` : ''}
      ${queueItemActions(item)}
      ${preview}
    </div>`;
  }
  const jobs = item.jobs || [];
  const jobsHtml = jobs.length
    ? `<div class="queue-linked-jobs">${jobs.map(job => `<span class="badge b-dim">split #${esc(job.id)} · ${esc(job.status)}</span>`).join('')}</div>`
    : '<div class="mono dim">Связанных split-задач пока нет.</div>';
  return `<div class="queue-item-details">
    <div class="mono dim">Источник: ${esc(item.source_path || '—')}</div>
    ${item.output_dir ? `<div class="mono dim">Output: ${esc(item.output_dir)}</div>` : ''}
    ${item.error ? `<div class="err">Ошибка: ${esc(shortErrorText(item.error))}</div>` : ''}
    ${item.kind === 'source' ? jobsHtml : ''}
    ${queueItemActions(item)}
  </div>`;
}
function renderQueueItems(targetId, rows) {
  const el = document.getElementById(targetId);
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = '<div class="empty">Очередь пуста. Импортируйте исходники или запустите цикл в разделе «Конвейер».</div>';
    return;
  }
  if (uiState.queueViewMode === 'grid') {
    el.innerHTML = `<div class="queue-card-grid">${rows.map(item => {
      const selected = item.kind === 'source' && selectedVideoIds.has(Number(item.video_id));
      const expanded = expandedQueueItemIds.has(item.id);
      const sourceCheck = item.kind === 'source'
        ? `<label class="media-card-check" onclick="event.stopPropagation()"><input type="checkbox" ${selected ? 'checked' : ''} onchange="toggleQueueSourceSelection(${Number(item.video_id)}, this.checked)"></label>`
        : '';
      return `<article class="queue-card ${expanded ? 'expanded' : ''} ${selected ? 'selected' : ''}" onclick="toggleQueueItemExpanded('${esc(item.id)}')">
        <div class="queue-card-thumb">${sourceCheck}${videoThumb(item.source_path || '', item.title || item.id)}</div>
        <div class="queue-card-body">
          <div class="queue-card-top">${queueItemKindBadge(item)}${badge(item.status)}${item.kind === 'render' ? badge(item.review_status || 'pending') : ''}${queueSourceStateBadge(item)}</div>
          <div class="queue-card-title" title="${esc(item.title || '')}">${esc(item.title || item.id)}</div>
          <div class="mono dim ov">${esc(shortPath(item.source_path || item.output_dir || '—'))}</div>
          ${queueProgressBar(item)}
          ${expanded ? queueItemDetails(item) : `<div class="queue-card-actions">${queueItemActions(item)}</div>`}
        </div>
      </article>`;
    }).join('')}</div>`;
    renderQueueSourceBulkToolbar();
    return;
  }
  el.innerHTML = `<div class="table-scroll"><table class="tbl queue-unified-table"><thead><tr><th></th><th>Элемент</th><th>Тип</th><th>Статус</th><th>Файл</th><th>Счётчики</th><th>Прогресс</th><th>Действия</th></tr></thead><tbody>${rows.map(item => {
    const selected = item.kind === 'source' && selectedVideoIds.has(Number(item.video_id));
    const expanded = expandedQueueItemIds.has(item.id);
    const checkbox = item.kind === 'source'
      ? `<input type="checkbox" ${selected ? 'checked' : ''} onclick="event.stopPropagation()" onchange="toggleQueueSourceSelection(${Number(item.video_id)}, this.checked)">`
      : '';
    const counts = item.kind === 'source'
      ? `метки ${Number(item.counts?.marks || 0)} · сегм. ${Number(item.counts?.segments || 0)} · клипы ${Number(item.counts?.clips || 0)}`
      : Object.entries(item.counts || {}).map(([key, value]) => `${esc(key)} ${esc(value)}`).join(' · ');
    const statusStack = `${badge(item.status)}${item.kind === 'render' ? badge(item.review_status || 'pending') : ''}${queueSourceStateBadge(item)}`;
    const subtitle = item.kind === 'render'
      ? `${item.channel_profile_name ? esc(item.channel_profile_name) + ' · ' : ''}${esc(item.template_key || item.template_name || '')}`
      : `#${esc(item.id)}`;
    return `<tr class="queue-row ${expanded ? 'expanded' : ''}" data-kind="${esc(item.kind)}" onclick="toggleQueueItemExpanded('${esc(item.id)}')"><td>${checkbox}</td><td><div class="video-name-cell">${videoThumb(item.source_path || '', item.title || item.id)}<div style="min-width:0;flex:1"><div class="mono txt ov">${esc(item.title || item.id)}</div><div class="mono dim">${subtitle}</div></div></div></td><td>${queueItemKindBadge(item)}</td><td><div class="status-stack">${statusStack}</div></td><td><span class="mono dim ov" title="${esc(item.source_path || item.output_dir || '')}">${esc(shortPath(item.source_path || item.output_dir || '—'))}</span>${item.output_dir ? `<div class="mono dim ov">Output: ${esc(shortPath(item.output_dir))}</div>` : ''}</td><td class="mono mid">${esc(counts || '—')}</td><td>${queueProgressBar(item)}</td><td>${queueItemActions(item)}</td></tr>${expanded ? `<tr class="queue-details-row"><td></td><td colspan="7">${queueItemDetails(item)}</td></tr>` : ''}`;
  }).join('')}</tbody></table></div>`;
  renderQueueSourceBulkToolbar();
}

async function loadVideos() {
  const target = document.getElementById('videos-table');
  if (target && !lastVideos.length) {
    target.innerHTML = '<div class="empty">Загружаю список видео…</div>';
  }
  try {
    const data = await api.get('/api/videos');
    lastVideos = data.videos || [];
    selectedVideoIds = new Set(Array.from(selectedVideoIds).filter(id => lastVideos.some(video => Number(video.id) === Number(id))));
    renderVideoCounts(data.counts || {});
    renderVideosTable(getVisibleVideos());
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
  videoFilter = status || 'all';
  renderVideosTable(getVisibleVideos());
}
function getVisibleVideos() {
  return videoFilter === 'all'
    ? lastVideos
    : lastVideos.filter(video => video.review_status === videoFilter);
}
function selectedVideos() {
  return lastVideos.filter(video => selectedVideoIds.has(Number(video.id)));
}
function setVideoViewMode(mode) {
  uiState.videoViewMode = mode === 'grid' ? 'grid' : 'table';
  writeUiPreference('videoViewMode', uiState.videoViewMode);
  setSegmentedState('[data-video-view]', uiState.videoViewMode, 'data-video-view');
  renderVideosTable(getVisibleVideos());
}
function setClipViewMode(mode) {
  uiState.clipViewMode = mode === 'grid' ? 'grid' : 'table';
  writeUiPreference('clipViewMode', uiState.clipViewMode);
  setSegmentedState('[data-clip-view]', uiState.clipViewMode, 'data-clip-view');
  renderClipsTable(getVisibleWorkspaceItems());
}
function videoInspectorBody(video) {
  const output = video.output_dir ? `<button class="btn-secondary" data-path="${esc(video.output_dir)}" onclick="goToOutputFolder(this.dataset.path)">Открыть папку сегментов</button>` : '<span class="mono dim">Папка сегментов ещё не создана</span>';
  const childCount = Number(video.segment_count || 0) + Number(video.clip_count || 0);
  return `<div class="inspector-section">
    <h3>Источник</h3>
    <div class="inspector-kv">
      <div><b>ID</b><span class="mono">#${Number(video.id)}</span></div>
      <div><b>Статус</b><span>${badge(video.review_status)}</span></div>
      <div><b>Длит.</b><span class="mono">${esc(video.duration_text || '—')}</span></div>
      <div><b>Метки</b><span>${Number(video.mark_count || 0)}</span></div>
      <div><b>Нарезки/клипы</b><span>${childCount}</span></div>
    </div>
  </div>
  <div class="inspector-section">
    <h3>Пути</h3>
    <div class="mono dim" style="overflow-wrap:anywhere">${esc(video.source_path || '')}</div>
    ${video.output_dir ? `<div class="mono dim" style="margin-top:8px;overflow-wrap:anywhere">${esc(video.output_dir)}</div>` : ''}
  </div>
  <div class="inspector-section">
    <h3>Действия</h3>
    <div class="row-actions">${mpvButton(video.source_path)}${output}<button class="btn-mini" onclick="openQueueClipsForVideo(${Number(video.id)})">Показать клипы</button><button class="btn-danger" onclick="deleteAllClipsForVideo(${Number(video.id)}, this.dataset.title)" data-title="${esc(video.title || `Видео #${video.id}`)}">Удалить все клипы этого видео</button><button class="btn-danger" onclick="openVideoDeleteDialog([${Number(video.id)}])">Удалить родительское видео</button></div>
  </div>`;
}
function openVideoInspector(videoId) {
  const video = lastVideos.find(item => Number(item.id) === Number(videoId));
  if (!video) return;
  openInspector({
    title: video.title || `Видео #${video.id}`,
    kicker: 'Source video',
    body: videoInspectorBody(video),
  });
}
function renderVideosBulkToolbar() {
  const el = document.getElementById('queue-sources-bulk-toolbar');
  if (!el) return;
  const visibleRows = getVisibleVideos();
  const selectedCount = selectedVideos().length;
  el.innerHTML = `
    <div class="row-actions">
      <button class="btn-mini" ${visibleRows.length ? '' : 'disabled'} onclick="setVisibleVideosSelected(true)">Выбрать в фильтре</button>
      <button class="btn-mini" ${selectedCount ? '' : 'disabled'} onclick="clearVideoSelection()">Снять выделение</button>
    </div>
    <div class="row-actions">
      <span class="mono dim">${selectedCount ? `Выбрано видео: ${selectedCount}` : 'Выберите одно или несколько видео'}</span>
      <button class="btn-danger" ${selectedCount ? '' : 'disabled'} onclick="deleteSelectedVideos()">Удалить выбранные</button>
    </div>`;
  syncVideosSelectAllCheckbox();
  renderVideosActionBar();
}
function renderQueueSourceBulkToolbar() {
  renderVideosBulkToolbar();
}
function renderVideosActionBar() {
  const count = selectedVideos().length;
  if (currentView !== 'queue' || !count) {
    if (currentView === 'queue') renderActionBar();
    return;
  }
  renderActionBar(`Видео выбрано: ${count}`, `
    <button class="btn-mini" onclick="clearVideoSelection()">Снять</button>
    <button class="btn-danger" onclick="deleteSelectedVideos()">Удалить</button>
  `);
}
function syncVideosSelectAllCheckbox() {
  const selectAll = document.getElementById('videos-select-all');
  if (!selectAll) return;
  const visibleRows = getVisibleVideos();
  selectAll.checked = Boolean(visibleRows.length && visibleRows.every(video => selectedVideoIds.has(Number(video.id))));
}
function toggleVideoSelection(videoId, checked) {
  const id = Number(videoId);
  if (!id) return;
  if (checked) selectedVideoIds.add(id);
  else selectedVideoIds.delete(id);
  renderVideosBulkToolbar();
  syncVideosSelectAllCheckbox();
}
function toggleQueueSourceSelection(videoId, checked) {
  toggleVideoSelection(videoId, checked);
  renderQueueItems('jobs-table', lastQueueItems);
}
function toggleVisibleVideosSelection(checked) {
  setVisibleVideosSelected(Boolean(checked));
}
function setVisibleVideosSelected(checked) {
  getVisibleVideos().forEach(video => {
    const id = Number(video.id);
    if (checked) selectedVideoIds.add(id);
    else selectedVideoIds.delete(id);
  });
  renderQueueItems('jobs-table', lastQueueItems);
}
function clearVideoSelection() {
  selectedVideoIds.clear();
  renderQueueItems('jobs-table', lastQueueItems);
  renderQueueSourceBulkToolbar();
}
async function deleteSelectedVideos() {
  openVideoDeleteDialog(Array.from(selectedVideoIds));
}
function videoDeleteDialogElement() {
  let el = document.getElementById('video-delete-modal');
  if (!el) {
    el = document.createElement('div');
    el.id = 'video-delete-modal';
    el.className = 'modal-backdrop video-delete-backdrop';
    el.style.display = 'none';
    el.addEventListener('click', event => {
      if (event.target === el) closeVideoDeleteDialog();
    });
    document.body.appendChild(el);
  }
  return el;
}
function openVideoDeleteDialog(videoIds = null) {
  const ids = (videoIds || Array.from(selectedVideoIds))
    .map(value => Number(value))
    .filter(value => Number.isFinite(value) && value > 0);
  if (!ids.length) {
    showToast('Выберите видео для удаления', 'err');
    return;
  }
  pendingVideoDeleteIds = Array.from(new Set(ids));
  const videos = pendingVideoDeleteIds.map(id => lastVideos.find(video => Number(video.id) === id)).filter(Boolean);
  const childCount = videos.reduce((sum, video) => sum + Number(video.segment_count || 0) + Number(video.clip_count || 0), 0);
  const titleList = videos.slice(0, 4).map(video => `<li title="${esc(video.source_path || '')}">${esc(video.title || `Видео #${video.id}`)}</li>`).join('');
  const extra = videos.length > 4 ? `<li class="dim">…и ещё ${videos.length - 4}</li>` : '';
  const el = videoDeleteDialogElement();
  el.innerHTML = `<section class="schedule-modal video-delete-panel" onclick="event.stopPropagation()">
    <div class="schedule-modal-head">
      <span>Удаление родительского видео</span>
      <button class="btn-mini" onclick="closeVideoDeleteDialog()">×</button>
    </div>
    <div class="schedule-modal-body">
      <div class="delete-summary">
        <b>Будет скрыто из списка исходников: ${pendingVideoDeleteIds.length}</b>
        <p>Клипы останутся видимыми и будут привязаны к удалённому исходнику.</p>
        ${titleList ? `<ul>${titleList}${extra}</ul>` : ''}
      </div>
      <label class="android-switch-row">
        <span>
          <b>Удалить исходные файлы с диска</b>
          <small>Если выключено, файл-источник останется на месте.</small>
        </span>
        <input id="video-delete-source-files" type="checkbox">
        <i aria-hidden="true"></i>
      </label>
      <label class="android-switch-row danger">
        <span>
          <b>Удалить нарезки и клипы вместе с видео</b>
          <small>${childCount ? `Затронет примерно ${childCount} элементов. ` : ''}Если выключено, клипы останутся видимыми.</small>
        </span>
        <input id="video-delete-child-clips" type="checkbox">
        <i aria-hidden="true"></i>
      </label>
      <label class="android-switch-row">
        <span>
          <b>Удалить видео из локальных профилей</b>
          <small>Если включено, связанные карточки исчезнут из профилей. Файлы удаляются только переключателями выше.</small>
        </span>
        <input id="video-delete-profile-items" type="checkbox">
        <i aria-hidden="true"></i>
      </label>
      <div class="row-actions end">
        <button class="btn-secondary" onclick="closeVideoDeleteDialog()">Отмена</button>
        <button class="btn-danger" onclick="confirmVideoDeleteDialog()">Удалить</button>
      </div>
    </div>
  </section>`;
  el.style.display = 'grid';
}
function closeVideoDeleteDialog() {
  pendingVideoDeleteIds = [];
  const el = document.getElementById('video-delete-modal');
  if (el) el.style.display = 'none';
}
async function confirmVideoDeleteDialog() {
  const ids = pendingVideoDeleteIds.slice();
  const deleteSourceFiles = Boolean(document.getElementById('video-delete-source-files')?.checked);
  const deleteChildClips = Boolean(document.getElementById('video-delete-child-clips')?.checked);
  const removeFromProfiles = Boolean(document.getElementById('video-delete-profile-items')?.checked);
  closeVideoDeleteDialog();
  await performVideoDelete(ids, {deleteSourceFiles, deleteChildClips, removeFromProfiles});
}
async function performVideoDelete(ids, options = {}) {
  const normalized = (ids || []).map(value => Number(value)).filter(value => Number.isFinite(value) && value > 0);
  if (!normalized.length) {
    showToast('Выберите видео для удаления', 'err');
    return;
  }
  const deleteSourceFiles = Boolean(options.deleteSourceFiles);
  const deleteChildClips = Boolean(options.deleteChildClips);
  const removeFromProfiles = Boolean(options.removeFromProfiles);
  try {
    const data = await api.post('/api/videos/bulk-delete', {
      video_ids: normalized,
      delete_source_files: deleteSourceFiles,
      delete_child_clips: deleteChildClips,
      remove_from_profiles: removeFromProfiles,
    });
    lastVideos = data.videos || [];
    normalized.forEach(id => selectedVideoIds.delete(id));
    renderVideoCounts(data.counts || {});
    renderVideosTable(getVisibleVideos());
    const summary = data.summary || {};
    const source = summary.source_files || {};
    const child = summary.child_clips || {};
    const profileItems = summary.profile_items || {};
    const filePart = deleteSourceFiles ? ` · исходников удалено: ${source.deleted || 0}` : '';
    const childPart = deleteChildClips ? ` · клипов удалено: ${child.hidden || 0}` : ' · клипы сохранены';
    const profilePart = removeFromProfiles ? ` · из профилей удалено: ${profileItems.removed || 0}` : '';
    showToast(`Скрыто исходников: ${summary.deleted || 0}${childPart}${filePart}${profilePart}`);
    await Promise.allSettled([loadDashboard(), loadJobs(), loadClips()]);
    closeInspector();
  } catch (err) {
    showToast(err.message || 'Не удалось удалить видео', 'err');
  }
}
function renderVideosTable(rows) {
  const el = document.getElementById('videos-table');
  if (!el) return;
  renderVideosBulkToolbar();
  if (!rows.length) { el.innerHTML = '<div class="empty">Нет видео</div>'; return; }
  if (uiState.videoViewMode === 'grid') {
    el.innerHTML = `<div class="media-grid">${rows.map(video => {
      const selected = selectedVideoIds.has(Number(video.id));
      return `<article class="media-card ${selected ? 'selected' : ''}" onclick="openVideoInspector(${Number(video.id)})">
        <div class="media-card-thumb">
          <label class="media-card-check" onclick="event.stopPropagation()"><input type="checkbox" ${selected ? 'checked' : ''} onchange="toggleVideoSelection(${Number(video.id)}, this.checked);renderVideosTable(getVisibleVideos())"></label>
          ${videoThumb(video.source_path, video.title)}
        </div>
        <div class="media-card-body">
          <div class="media-card-title" title="${esc(video.title)}">${esc(video.title)}</div>
          <div class="media-card-meta">${badge(video.review_status)} · ${esc(video.duration_text || '—')} · метки ${Number(video.mark_count || 0)} · клипы ${Number(video.clip_count || 0)}</div>
          <div class="media-card-path" title="${esc(video.source_path)}">${esc(shortPath(video.source_path))}</div>
          <div class="media-card-actions">${mpvButton(video.source_path)}${outputFolderButton(video.output_dir)}</div>
        </div>
      </article>`;
    }).join('')}</div>`;
    return;
  }
  const allSelected = rows.length && rows.every(video => selectedVideoIds.has(Number(video.id)));
  el.innerHTML = `<div class="table-scroll"><table class="tbl"><thead><tr><th><input id="videos-select-all" type="checkbox" ${allSelected ? 'checked' : ''} onchange="toggleVisibleVideosSelection(this.checked)"></th><th>#</th><th>Название</th><th>Длительность</th><th>Статус</th><th class="r">Метки</th><th class="r">Клипы</th><th>Источник</th><th>Действие</th></tr></thead><tbody>${rows.map(video => {
    const selected = selectedVideoIds.has(Number(video.id));
    const title = video.output_dir
      ? `<button class="link-video mono txt ov" data-path="${esc(video.output_dir)}" title="Открыть папку сегментов: ${esc(video.output_dir)}" onclick="event.stopPropagation();goToOutputFolder(this.dataset.path)">${esc(video.title)}</button>`
      : `<span class="mono txt ov" title="${esc(video.source_path)}">${esc(video.title)}</span>`;
    return `<tr data-s="${esc(video.review_status)}" onclick="openVideoInspector(${Number(video.id)})"><td><input type="checkbox" ${selected ? 'checked' : ''} onclick="event.stopPropagation()" onchange="toggleVideoSelection(${Number(video.id)}, this.checked)"></td><td class="mono dim">#${video.id}</td><td><div class="video-name-cell">${videoThumb(video.source_path, video.title)}<div style="min-width:0;flex:1">${title}${video.output_dir ? `<div class="mono dim ov" title="${esc(video.output_dir)}">Сегменты: ${esc(shortPath(video.output_dir))}</div>` : ''}</div></div></td><td class="mono mid">${esc(video.duration_text)}</td><td>${badge(video.review_status)}</td><td class="mono txt r">${video.mark_count}</td><td class="mono warn r">${video.clip_count}</td><td><span class="mono dim ov">${esc(shortPath(video.source_path))}</span></td><td><div class="row-actions">${mpvButton(video.source_path)}${outputFolderButton(video.output_dir)}</div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

async function loadClips() {
  const target = document.getElementById('clips-table');
  if (target && !lastClips.length) {
    target.innerHTML = '<div class="empty">Загружаю клипы и теги workspace…</div>';
  }
  try {
    const [data, profilesData, templatesData, reactionsData] = await Promise.all([
      api.get('/api/workspace/clips'),
      api.get('/api/editing/channel-profiles?enabled=true'),
      api.get('/api/studio/templates?status=active'),
      api.get('/api/editing/reactions?enabled=true'),
      loadCatalogTags().catch(() => null),
    ]);
    lastClips = data.items || [];
    editingProfiles = profilesData.items || editingProfiles || [];
    editingStudioTemplates = studioTemplateOptions(templatesData.items || editingStudioTemplates || []);
    editingReactions = reactionsData.items || editingReactions || [];
    syncWorkspaceEditingSelection();
    if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) {
      currentWorkspaceItemKey = null;
    }
    renderClipCounts(workspaceCountsFromItems(workspaceItemsForParentFilter()));
    renderWorkspaceTagControls();
    renderWorkspaceEditingControls();
    renderWorkspaceParentFilterLine();
    renderClipsTable(getVisibleWorkspaceItems());
    if (!workspaceDetailDirty) renderWorkspaceDetail();
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
function renderWorkspaceParentFilterLine() {
  const el = document.getElementById('workspace-parent-filter-line');
  if (!el) return;
  if (!workspaceParentVideoFilter) {
    el.innerHTML = 'Показаны все нарезки и клипы.';
    return;
  }
  const deleteButton = workspaceParentVideoFilter.videoId
    ? `<button class="btn-danger btn-mini" data-title="${esc(workspaceParentVideoFilter.title || 'исходник')}" onclick="deleteAllClipsForVideo(${Number(workspaceParentVideoFilter.videoId)}, this.dataset.title)">Удалить все клипы этого исходника</button>`
    : '';
  el.innerHTML = `<span>Показаны клипы исходника: <b>${esc(workspaceParentVideoFilter.title || 'исходник')}</b></span>${deleteButton}`;
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
function workspaceItemMatchesParentFilter(item) {
  if (!workspaceParentVideoFilter) return true;
  const filterVideoId = Number(workspaceParentVideoFilter.videoId || 0);
  if (filterVideoId && Number(item?.video_id || 0) === filterVideoId) return true;
  const filterSourcePath = normalizeWorkspaceFilterPath(workspaceParentVideoFilter.sourcePath || '');
  if (filterSourcePath && normalizeWorkspaceFilterPath(item?.source_path || '') === filterSourcePath) return true;
  return false;
}
function workspaceItemsForParentFilter() {
  return lastClips.filter(workspaceItemMatchesParentFilter);
}
function filterClips(tab, status) {
  workspaceFilter = status || 'all';
  tab.closest('.tabs').querySelectorAll('.tab').forEach(item => item.classList.remove('on'));
  tab.classList.add('on');
  renderClipCounts(workspaceCountsFromItems(workspaceItemsForParentFilter()));
  renderWorkspaceFilterControls();
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
}
function workspaceItemSearchText(item) {
  return [
    item?.id,
    item?.title,
    item?.file_name,
    item?.video_title,
    item?.path,
    item?.workspace_path,
    item?.source_path,
    item?.tags,
    item?.workspace_status,
    ...(workspaceCatalogTags(item).map(tag => `${tag.name || ''} ${tag.slug || ''} ${tag.description || ''}`)),
  ].join(' ').toLowerCase();
}
function workspaceItemMatchesText(item) {
  const query = String(workspaceSearchQuery || '').trim().toLowerCase();
  if (!query) return true;
  return query.split(/\s+/).every(part => workspaceItemSearchText(item).includes(part));
}
function workspaceItemMatchesTagFilters(item) {
  const ids = new Set(workspaceCatalogTags(item).map(tag => Number(tag.id)));
  for (const tagId of workspaceFilterIncludeTagIds) {
    if (!ids.has(Number(tagId))) return false;
  }
  for (const tagId of workspaceFilterExcludeTagIds) {
    if (ids.has(Number(tagId))) return false;
  }
  return true;
}
function getVisibleWorkspaceItems() {
  const parentItems = workspaceItemsForParentFilter();
  const byStatus = workspaceFilter === 'missing'
    ? parentItems.filter(item => item.missing)
    : workspaceFilter === 'all'
      ? parentItems
      : parentItems.filter(item => item.workspace_status === workspaceFilter);
  return byStatus.filter(item => workspaceItemMatchesText(item) && workspaceItemMatchesTagFilters(item));
}
function workspaceTypeLabel(item) {
  return item?.item_type === 'clip' ? 'Клип' : 'Сегмент';
}
function workspaceTitle(item) {
  return item?.title || item?.file_name || `${workspaceTypeLabel(item)} #${item?.item_id || ''}`;
}
function targetAspectLabel(value) {
  const aspect = value || 'original';
  if (aspect === '16x9') return '16:9';
  if (aspect === '9x16') return '9:16';
  return 'Original';
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
const STORAGE_PROFILE_VIDEO_FOLDERS = ['edits', 'ready', 'published'];
function isStorageProfileVideoPath(path) {
  const text = String(path || '').trim();
  return STORAGE_PROFILE_VIDEO_FOLDERS.includes(text.split('/')[0]);
}
async function workspacePathForStorageProfile(path) {
  const relative = await workspaceMediaPathForPlayer(path);
  if (!isStorageProfileVideoPath(relative)) {
    throw new Error('В локальный профиль можно добавлять только готовые видео из Результатов монтажа, Готовых или Опубликованных.');
  }
  return relative;
}
function storageProfileById(id) {
  return storageProfiles.find(profile => Number(profile.id) === Number(id)) || null;
}
function storageProfileName(profile) {
  return profile?.effective_name || profile?.name || 'Профиль';
}
function storageProfileHandle(profile) {
  return profile?.effective_handle || profile?.handle || `profile-${profile?.id || ''}`;
}
function storageProfileDescription(profile) {
  return profile?.effective_description || profile?.description || 'Локальный профиль ShortsFarm для готового контента.';
}
function storageProfileInitials(profile) {
  return profile?.effective_avatar_initials || profile?.avatar_initials || String(storageProfileName(profile) || 'SF').trim().slice(0, 2).toUpperCase();
}
function storageProfileAvatarHtml(profile, className = 'storage-profile-icon') {
  const url = profile?.effective_avatar_url || profile?.avatar_url || '';
  const bg = profile?.effective_avatar_color || profile?.avatar_color || '#3b82f6';
  const initials = storageProfileInitials(profile);
  const image = url
    ? `<img class="storage-avatar-img" src="${esc(url)}" alt="" onerror="this.style.display='none'">`
    : '';
  return `<span class="${esc(className)} storage-avatar-wrap" style="--avatar-bg:${esc(bg)}">${image}<span class="storage-avatar-fallback">${esc(initials)}</span></span>`;
}
function storageProfileBannerStyle(profile) {
  const url = profile?.effective_banner_url || profile?.banner_url || '';
  const color = profile?.effective_banner_color || profile?.banner_color || '#111827';
  const safeUrl = encodeURI(String(url || '')).replace(/[()'"]/g, encodeURIComponent);
  return url
    ? `background-color:${esc(color)};background-image:linear-gradient(180deg,rgba(2,6,23,.08),rgba(2,6,23,.38)),url(&quot;${esc(safeUrl)}&quot;);`
    : `background:${esc(color)};`;
}
function tagPill(tag, options = {}) {
  if (!tag) return '';
  const cls = ['tag-pill'];
  if (tag.kind) cls.push(`tag-${String(tag.kind)}`);
  if (options.locked || tag.locked) cls.push('locked');
  const color = tag.color || '#64748b';
  const label = tag.name || tag.slug || `tag-${tag.id}`;
  return `<span class="${cls.join(' ')}" style="--tag-color:${esc(color)}" title="${esc(tag.slug || label)}">${esc(label)}</span>`;
}
function tagListPills(tags) {
  const list = Array.isArray(tags) ? tags : [];
  return list.length ? `<div class="tag-pill-list">${list.map(tagPill).join('')}</div>` : '<div class="mono dim">тегов пока нет</div>';
}
async function loadCatalogTags(options = {}) {
  if (catalogTags.length && !options.force) return catalogTags;
  const data = await api.get('/api/tags?enabled=true&limit=1000');
  catalogTags = data.items || [];
  return catalogTags;
}
function tagKindLabel(kind) {
  const value = String(kind || 'user');
  if (value === 'status') return 'статус';
  if (value === 'channel') return 'канал';
  if (value === 'system') return 'системный';
  return 'пользовательский';
}
function catalogAssignableTags() {
  return (catalogTags || []).filter(tag => tag.enabled !== false && tag.kind !== 'status');
}
function tagOptionsHtml(selectedIds = [], options = {}) {
  const selected = new Set((selectedIds || []).map(Number));
  let tags = options.assignableOnly ? catalogAssignableTags() : catalogTags.filter(tag => tag.kind !== 'status' || tag.slug === 'status-ready');
  if (options.onlySelected) tags = tags.filter(tag => selected.has(Number(tag.id)));
  const empty = options.emptyLabel ? `<option value="">${esc(options.emptyLabel)}</option>` : '';
  return empty + tags
    .map(tag => `<option value="${Number(tag.id)}"${selected.has(Number(tag.id)) ? ' selected' : ''}>${esc(tag.name)} · ${esc(tag.slug)}</option>`)
    .join('');
}
function catalogTagIds(tags, options = {}) {
  const includeStatus = options.includeStatus !== false;
  return (tags || [])
    .filter(tag => includeStatus || tag.kind !== 'status')
    .map(tag => Number(tag.id))
    .filter(Boolean);
}
function workspaceCatalogTags(item) {
  return Array.isArray(item?.catalog_tags) ? item.catalog_tags : [];
}
function workspaceCatalogPath(item) {
  return String(item?.workspace_path || '').trim();
}
function updateWorkspaceItemCatalogTags(workspacePath, tags, updatedItem = null) {
  if (!workspacePath) return;
  lastClips = lastClips.map(item => {
    if (workspaceCatalogPath(item) !== workspacePath) return item;
    const next = {...item, catalog_tags: tags || []};
    if (updatedItem?.workspace_status) next.workspace_status = updatedItem.workspace_status;
    if (updatedItem?.title !== undefined) next.title = updatedItem.title || next.title;
    return next;
  });
  if (currentWorkspaceItemKey) {
    const current = workspaceItemByKey(currentWorkspaceItemKey);
    if (current && workspaceCatalogPath(current) === workspacePath && updatedItem?.workspace_status) {
      current.workspace_status = updatedItem.workspace_status;
    }
  }
  tagManagerVideoResults = tagManagerVideoResults.map(item => (
    item.workspace_path === workspacePath ? {...item, tags: tags || [], is_publish_ready: (tags || []).some(tag => tag.slug === 'status-ready')} : item
  ));
  storageProfileCandidates = storageProfileCandidates.map(item => (
    item.workspace_path === workspacePath ? {...item, tags: tags || [], is_publish_ready: (tags || []).some(tag => tag.slug === 'status-ready')} : item
  ));
}
function storageProfileErrorTarget() {
  return currentView === 'storage-profile' ? 'storage-profile-error' : 'storage-profiles-error';
}
function showStorageProfileError(message) {
  showInlineError(storageProfileErrorTarget(), message);
}
function hideStorageProfileErrors() {
  hideInlineError('storage-profiles-error');
  hideInlineError('storage-profile-error');
}
function storageProfileUrl(profileId = null) {
  const url = new URL(window.location.href);
  url.searchParams.delete('batch');
  url.searchParams.delete('project');
  if (profileId) url.searchParams.set('profile', String(Number(profileId)));
  else url.searchParams.delete('profile');
  return `${url.pathname}${url.search}${url.hash}`;
}
function setStorageProfileRoute(profileId, options = {}) {
  const nextUrl = storageProfileUrl(profileId);
  if (options.replace) window.history.replaceState({}, '', nextUrl);
  else window.history.pushState({}, '', nextUrl);
}
function storageProfileCard(profile) {
  const active = currentView === 'storage-profile' && Number(profile.id) === Number(currentStorageProfileId);
  const youtube = storageProfileYoutubeLink(profile);
  return `<button class="storage-profile-card${active ? ' active' : ''}" onclick="selectStorageProfile(${Number(profile.id)})">
    ${storageProfileAvatarHtml(profile, 'storage-profile-icon')}
    <span class="storage-profile-card-title">${esc(storageProfileName(profile))}</span>
    <span class="mono dim">@${esc(storageProfileHandle(profile))} · ${Number(profile.item_count || 0)} видео</span>
    ${youtube ? `<span class="badge b-err"><i class="ti ti-brand-youtube"></i>${esc(youtube.display_name || 'YouTube')}</span>` : ''}
    <span class="storage-profile-open-hint">Открыть профиль</span>
  </button>`;
}
function renderStorageProfilesGrid() {
  const el = document.getElementById('storage-profiles-grid');
  if (!el) return;
  el.innerHTML = `<button class="storage-profile-card create-card" onclick="createStorageProfile()">
    <span class="storage-profile-plus">+</span>
    <span class="storage-profile-card-title">Создать профиль</span>
    <span class="mono dim">локальная витрина</span>
  </button>${storageProfiles.map(storageProfileCard).join('')}`;
}
async function loadStorageProfiles(options = {}) {
  hideStorageProfileErrors();
  try {
    const [data] = await Promise.all([
      api.get('/api/storage-profiles'),
      loadCatalogTags().catch(() => null),
    ]);
    storageProfiles = data.items || [];
    if (options.selectId) currentStorageProfileId = Number(options.selectId);
    if (currentStorageProfileId && !storageProfileById(currentStorageProfileId)) {
      currentStorageProfileId = null;
      currentStorageProfile = null;
      storageProfileItems = [];
    }
    renderStorageProfilesGrid();
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось загрузить профили');
  }
}
async function createStorageProfile() {
  const name = await openTextActionModal({
    title: 'Создать локальный профиль',
    label: 'Название профиля',
    placeholder: 'Например: Anime Shorts',
    confirmText: 'Создать профиль',
    validate: value => value ? '' : 'Введите название профиля.',
  });
  if (!name) return;
  try {
    const data = await api.post('/api/storage-profiles', {name});
    const profile = data.profile;
    showToast('Профиль создан');
    await loadStorageProfiles({selectId: profile.id});
    await openStorageProfile(profile.id);
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось создать профиль');
  }
}
async function selectStorageProfile(profileId) {
  await openStorageProfile(profileId);
}
async function openStorageProfile(profileId, options = {}) {
  currentStorageProfileId = Number(profileId);
  storageCandidatePickerOpen = false;
  storageProfileActiveTab = options.tab || 'publish';
  storageProfileDrawerOpen = false;
  storageProfileDrawerSection = 'publish';
  selectedStorageProfileItemIds.clear();
  hideStorageProfileErrors();
  activateView('storage-profile', document.querySelector('[data-v="storage-profiles"]'));
  setStorageProfileRoute(currentStorageProfileId, {replace: Boolean(options.replace)});
  await loadStorageProfileDetail(currentStorageProfileId);
}
async function openStorageProfilesHub(options = {}) {
  hideStorageProfileErrors();
  activateView('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
  if (!options.keepUrl) {
    const nextUrl = storageProfileUrl(null);
    if (options.replace) window.history.replaceState({}, '', nextUrl);
    else window.history.pushState({}, '', nextUrl);
  }
  await loadStorageProfiles();
}
async function loadStorageProfileDetail(profileId = currentStorageProfileId) {
  if (!profileId) {
    renderStorageProfileDetail();
    return;
  }
  try {
    const [data, jobsData, youtubeVideosData] = await Promise.all([
      api.get(`/api/storage-profiles/${Number(profileId)}`),
      api.get(`/api/storage-profiles/${Number(profileId)}/publish-jobs?limit=200`).catch(() => ({jobs: []})),
      api.get(`/api/storage-profiles/${Number(profileId)}/youtube/videos?limit=200`).catch(() => ({videos: []})),
      loadStorageYoutubeAccounts().catch(() => null),
      loadCatalogTags().catch(() => null),
      loadEditingSupportData().catch(() => null),
    ]);
    currentStorageProfile = data.profile;
    currentStorageProfileId = Number(data.profile.id);
    storageProfileItems = data.items || [];
    storageProfilePublishJobs = jobsData.jobs || [];
    storageProfileYoutubeVideos = youtubeVideosData.videos || [];
    selectedStorageProfileItemIds = new Set(Array.from(selectedStorageProfileItemIds).filter(id => storageProfileItems.some(item => Number(item.id) === Number(id))));
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось загрузить профиль');
  }
}
async function loadStorageYoutubeAccounts() {
  const data = await api.get('/api/publish/youtube/accounts');
  storageYoutubeAccounts = (data.accounts || []).filter(account => (account.status || 'active') === 'active');
  lastYoutubeAccounts = data.accounts || lastYoutubeAccounts;
  return storageYoutubeAccounts;
}
function storageProfileYoutubeLink(profile) {
  return (profile?.service_links || []).find(link => link.platform === 'youtube' && link.status === 'linked') || null;
}
function storageAccountTitle(account) {
  if (!account) return 'YouTube канал';
  const email = account.account_email ? ` · ${account.account_email}` : '';
  return `${account.channel_title || account.display_name || `Канал #${account.id}`}${email}`;
}
function storageProfileServiceLinks(profile) {
  const youtube = storageProfileYoutubeLink(profile);
  const linkedAccount = youtube?.youtube_account || null;
  const selectedId = linkedAccount?.id || youtube?.external_account_id || storageYoutubeAccounts[0]?.id || '';
  const accountOptions = storageYoutubeAccounts.map(account => (
    `<option value="${Number(account.id)}"${Number(account.id) === Number(selectedId) ? ' selected' : ''}>${esc(storageAccountTitle(account))}</option>`
  )).join('');
  const accountControls = storageYoutubeAccounts.length
    ? `<div class="storage-youtube-controls">
        <select id="storage-profile-youtube-account">${accountOptions}</select>
        <button class="btn-secondary" onclick="linkStorageProfileYoutube()">${youtube ? 'Сменить канал' : 'Привязать YouTube'}</button>
        ${youtube ? '<button class="btn-secondary" onclick="syncStorageProfileYoutubeBranding()">Обновить оформление с YouTube</button>' : ''}
        ${youtube ? '<button class="btn-danger" onclick="unlinkStorageProfileYoutube()">Отвязать</button>' : ''}
      </div>`
    : `<div class="storage-youtube-controls">
        <button class="btn-secondary" onclick="openStorageProfileYoutubeSettings()">Подключить YouTube-канал</button>
      </div>`;
  const branding = profile?.youtube_branding || {};
  const brandingState = youtube
    ? `<div class="storage-youtube-branding">
        <label class="workspace-youtube-toggle">
          <input type="checkbox" ${branding.sync_enabled !== false ? 'checked' : ''} onchange="toggleStorageProfileYoutubeBranding(this.checked)">
          <span>Автоматически брать оформление из YouTube</span>
        </label>
        <div class="mono dim">${branding.synced_at ? `Успешный sync: ${esc(formatMtime(branding.synced_at))}` : 'Оформление ещё не синхронизировалось.'}${branding.attempted_at && !branding.synced_at ? ` · попытка: ${esc(formatMtime(branding.attempted_at))}` : ''}</div>
        ${branding.sync_error ? `<div class="mono err">Ошибка оформления: ${esc(shortErrorText(branding.sync_error))}</div>` : ''}
      </div>`
    : '';
  const status = youtube
    ? `<b>Привязан YouTube: ${esc(storageAccountTitle(linkedAccount) || youtube.display_name || 'канал')}</b><p>Выбирайте видео ниже: очередь, таймер и запуск публикации доступны прямо в профиле.</p>`
    : '<b>YouTube не привязан</b><p>Выберите уже подключённый канал. После привязки можно будет отправлять выбранные видео в очередь и на таймер прямо отсюда.</p>';
  return `<div class="storage-service-note storage-youtube-link"><i class="ti ti-brand-youtube"></i><div>${status}${brandingState}${accountControls}</div></div>`;
}
function storageBrandingOverrideAction(profile, field, localLabel, youtubeLabel) {
  const youtube = storageProfileYoutubeLink(profile);
  if (!youtube) return '';
  const branding = profile?.youtube_branding || {};
  const enabled = branding.overrides?.[field] === true;
  const label = enabled ? youtubeLabel : localLabel;
  const next = enabled ? 'false' : 'true';
  return `<button class="btn-mini" type="button" onclick="setStorageProfileBrandingOverride('${esc(field)}', ${next})">${esc(label)}</button>`;
}
function storageBrandingFieldActions(profile, field, localLabel, youtubeLabel) {
  const action = storageBrandingOverrideAction(profile, field, localLabel, youtubeLabel);
  return action ? `<div class="field-actions">${action}</div>` : '';
}
function setStorageProfileTab(tab) {
  const allowed = new Set(['publish', 'videos', 'queue', 'youtube', 'errors']);
  storageProfileActiveTab = allowed.has(tab) ? tab : 'publish';
  renderStorageProfileDetail();
}
function openStorageProfileDrawer(section = 'publish') {
  storageProfileDrawerOpen = true;
  storageProfileDrawerSection = section || 'publish';
  if (storageProfileDrawerSection === 'add-video') storageCandidatePickerOpen = true;
  renderStorageProfileDetail();
}
function closeStorageProfileDrawer() {
  if (storageProfileDrawerSection === 'add-video') {
    storageCandidatePickerOpen = false;
    selectedStorageCandidatePaths.clear();
  }
  storageProfileDrawerOpen = false;
  renderStorageProfileDetail();
}
function storageProfileTabButton(id, label, count = null) {
  const active = storageProfileActiveTab === id;
  const countHtml = count === null || count === undefined ? '' : `<span>${Number(count) || 0}</span>`;
  return `<button class="storage-profile-tab${active ? ' active' : ''}" onclick="setStorageProfileTab('${esc(id)}')">${esc(label)}${countHtml}</button>`;
}
function storageProfileRulesByMode(mode) {
  return (currentStorageProfile?.tag_rules || []).filter(rule => rule.mode === mode);
}
function storageProfileTagRulesPanel(profile) {
  const includeRules = storageProfileRulesByMode('include');
  const excludeRules = storageProfileRulesByMode('exclude');
  const includeIds = includeRules.map(rule => Number(rule.tag_id));
  const excludeIds = excludeRules.map(rule => Number(rule.tag_id));
  const matchMode = profile?.tag_match_mode || 'any';
  const includePills = includeRules.length
    ? includeRules.map(rule => `${tagPill(rule.tag, {locked: rule.locked})}${rule.locked ? '' : `<button class="tag-remove" onclick="removeStorageProfileTagRule(${Number(rule.tag_id)}, 'include')">×</button>`}`).join('')
    : '<span class="mono dim">Добавьте теги, по которым профиль будет собирать видео.</span>';
  const excludePills = excludeRules.length
    ? excludeRules.map(rule => `${tagPill(rule.tag)}<button class="tag-remove" onclick="removeStorageProfileTagRule(${Number(rule.tag_id)}, 'exclude')">×</button>`).join('')
    : '<span class="mono dim">Исключающих тегов нет.</span>';
  return `<div class="storage-tag-panel">
    <div class="storage-tag-panel-head">
      <div>
        <div class="storage-section-title inline-title">Теги профиля</div>
        <div class="mono dim">Профиль подключает теги, а не папки. Channel-тег YouTube создаётся автоматически и заблокирован.</div>
      </div>
      <div class="row-actions">
        <button class="btn-secondary" onclick="runStorageProfileTagSync()">Обновить по тегам</button>
        <button class="btn-mini" onclick="openGlobalTagsView()">Открыть Теги</button>
      </div>
    </div>
    <div class="storage-tag-rule-grid">
      <div class="storage-tag-rule-box">
        <div class="field-lbl">Include tags</div>
        <div class="tag-pill-list">${includePills}</div>
        <div class="storage-tag-add">
          <select id="storage-profile-include-tag">${tagOptionsHtml(includeIds.concat(excludeIds))}</select>
          <button class="btn-secondary" onclick="addStorageProfileTagRule('include')">Добавить</button>
        </div>
      </div>
      <div class="storage-tag-rule-box">
        <div class="field-lbl">Exclude tags</div>
        <div class="tag-pill-list">${excludePills}</div>
        <div class="storage-tag-add">
          <select id="storage-profile-exclude-tag">${tagOptionsHtml(includeIds.concat(excludeIds))}</select>
          <button class="btn-secondary" onclick="addStorageProfileTagRule('exclude')">Исключить</button>
        </div>
      </div>
      <div class="storage-tag-rule-box compact">
        <div class="field-lbl">Режим совпадения</div>
        <select id="storage-profile-tag-match-mode" onchange="saveStorageProfileTagRules()">
          <option value="any"${matchMode === 'any' ? ' selected' : ''}>Любой include-тег</option>
          <option value="all"${matchMode === 'all' ? ' selected' : ''}>Все include-теги</option>
        </select>
      </div>
    </div>
  </div>`;
}
function storageProfileRuleIds(mode) {
  return storageProfileRulesByMode(mode).map(rule => Number(rule.tag_id));
}
async function saveStorageProfileTagRules(next = {}) {
  if (!currentStorageProfileId) return;
  const include = next.include || storageProfileRuleIds('include');
  const exclude = next.exclude || storageProfileRuleIds('exclude');
  const mode = next.mode || document.getElementById('storage-profile-tag-match-mode')?.value || currentStorageProfile?.tag_match_mode || 'any';
  try {
    const data = await api.patch(`/api/storage-profiles/${Number(currentStorageProfileId)}/tag-rules`, {
      include_tag_ids: include,
      exclude_tag_ids: exclude,
      tag_match_mode: mode,
    });
    currentStorageProfile = data.profile || currentStorageProfile;
    renderStorageProfileDetail();
    showToast('Теги профиля сохранены');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось сохранить теги профиля');
  }
}
async function addStorageProfileTagRule(mode) {
  const selectId = mode === 'exclude' ? 'storage-profile-exclude-tag' : 'storage-profile-include-tag';
  const tagId = Number(document.getElementById(selectId)?.value || 0);
  if (!tagId) return;
  const include = storageProfileRuleIds('include');
  const exclude = storageProfileRuleIds('exclude');
  if (mode === 'exclude') {
    await saveStorageProfileTagRules({
      include: include.filter(id => id !== tagId),
      exclude: Array.from(new Set(exclude.concat([tagId]))),
    });
  } else {
    await saveStorageProfileTagRules({
      include: Array.from(new Set(include.concat([tagId]))),
      exclude: exclude.filter(id => id !== tagId),
    });
  }
}
async function removeStorageProfileTagRule(tagId, mode) {
  const include = storageProfileRuleIds('include');
  const exclude = storageProfileRuleIds('exclude');
  await saveStorageProfileTagRules({
    include: mode === 'include' ? include.filter(id => id !== Number(tagId)) : include,
    exclude: mode === 'exclude' ? exclude.filter(id => id !== Number(tagId)) : exclude,
  });
}
function openGlobalTagsView() {
  nav('tags', document.querySelector('[data-v="tags"]'));
}
async function createGlobalCatalogTag() {
  if (currentView !== 'tags') {
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
    showToast('Введите название тега и выберите цвет');
  };
  setTimeout(focusCreate, currentView === 'tags' ? 0 : 150);
}
async function loadTagsView(options = {}) {
  const {silent = false} = options;
  if (!silent) hideInlineError('tags-error');
  try {
    await loadCatalogTags({force: true});
    renderGlobalTagsManager();
  } catch (err) {
    if (!silent) showInlineError('tags-error', err.message || 'Не удалось загрузить теги');
  }
}
function showGlobalTagsError(message) {
  if (currentView === 'tags') showInlineError('tags-error', message);
  else showStorageProfileError(message);
}
async function createCatalogTag({name, color = '#64748b'} = {}) {
  const cleanName = String(name || '').trim();
  if (!cleanName) return null;
  try {
    const data = await api.post('/api/tags', {name: cleanName, color, kind: 'user'});
    catalogTags = catalogTags.concat([data.tag]);
    renderStorageProfileDetail();
    renderGlobalTagsManager();
    renderWorkspaceTagControls();
    if (currentView === 'queue') renderWorkspaceListAndDetail();
    showToast('Тег создан');
    return data.tag;
  } catch (err) {
    showToast(err.message || 'Не удалось создать тег', 'err');
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
  const tags = catalogTags || [];
  const q = String(tagManagerTagQuery || '').trim().toLowerCase();
  const matchesTagQuery = tag => !q || [tag.name, tag.slug, tag.description, tag.kind].join(' ').toLowerCase().includes(q);
  const userTags = tags.filter(tag => tag.kind === 'user' && matchesTagQuery(tag));
  const channelTags = tags.filter(tag => tag.kind === 'channel' && matchesTagQuery(tag));
  const statusTags = tags.filter(tag => tag.kind === 'status' && matchesTagQuery(tag));
  const selectedCount = selectedTagManagerVideoPaths.size;
  const assignOptions = tagOptionsHtml([], {assignableOnly: true, emptyLabel: 'Выберите тег'});
  const results = tagManagerVideoResults.length
    ? tagManagerVideoResults.map(item => {
        const selected = selectedTagManagerVideoPaths.has(item.workspace_path);
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
          <input id="tags-manager-search" type="text" value="${esc(tagManagerTagQuery)}" placeholder="Название, slug, тип…" oninput="onGlobalTagManagerSearchInput(this.value)">
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
            <input id="tag-manager-video-search" type="text" value="${esc(tagManagerSearchQuery)}" placeholder="Название, путь или тег…" oninput="onTagManagerVideoSearchInput(this.value)">
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
  tagManagerTagQuery = String(value || '');
  renderGlobalTagsManager();
}
async function reloadCatalogTagsForUi() {
  await loadCatalogTags({force: true});
  renderGlobalTagsManager();
  renderStorageProfileDetail();
  renderWorkspaceTagControls();
  renderWorkspaceListAndDetail();
  showToast('Теги обновлены');
}
function catalogTagById(tagId) {
  return (catalogTags || []).find(tag => Number(tag.id) === Number(tagId)) || null;
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
    catalogTags = catalogTags.map(item => Number(item.id) === Number(tagId) ? data.tag : item);
    renderGlobalTagsManager();
    renderWorkspaceListAndDetail();
    showToast('Тег обновлён');
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
  showToast('Измените цвет через цветной квадрат в таблице тегов');
}
async function updateCatalogTagColor(tagId, color) {
  const tag = catalogTagById(tagId);
  if (!tag || tag.locked) return;
  try {
    const data = await api.patch(`/api/tags/${Number(tagId)}`, {color});
    catalogTags = catalogTags.map(item => Number(item.id) === Number(tagId) ? data.tag : item);
    renderGlobalTagsManager();
    renderWorkspaceListAndDetail();
    renderWorkspaceFilterControls();
    showToast('Цвет тега обновлён');
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
    catalogTags = catalogTags.filter(item => Number(item.id) !== Number(tagId));
    renderGlobalTagsManager();
    renderWorkspaceListAndDetail();
    showToast('Тег отключён');
  } catch (err) {
    showGlobalTagsError(err.message || 'Не удалось отключить тег');
  }
}
async function searchTagManagerVideos() {
  try {
    const data = await api.get(`/api/catalog/videos/search?q=${encodeURIComponent(tagManagerSearchQuery)}&scope=all&limit=80`);
    tagManagerVideoResults = data.items || [];
    selectedTagManagerVideoPaths = new Set(Array.from(selectedTagManagerVideoPaths).filter(path => tagManagerVideoResults.some(item => item.workspace_path === path)));
    renderGlobalTagsManager();
  } catch (err) {
    showGlobalTagsError(err.message || 'Не удалось найти видео');
  }
}
function onTagManagerVideoSearchInput(value) {
  tagManagerSearchQuery = String(value || '');
  clearTimeout(tagManagerSearchTimer);
  tagManagerSearchTimer = setTimeout(searchTagManagerVideos, 250);
}
async function loadRandomTagManagerVideos() {
  try {
    const data = await api.get('/api/catalog/videos/random?scope=all&limit=32');
    tagManagerVideoResults = data.items || [];
    selectedTagManagerVideoPaths.clear();
    renderGlobalTagsManager();
  } catch (err) {
    showGlobalTagsError(err.message || 'Не удалось загрузить случайные видео');
  }
}
function toggleTagManagerVideoSelection(workspacePath, checked) {
  if (checked) selectedTagManagerVideoPaths.add(workspacePath);
  else selectedTagManagerVideoPaths.delete(workspacePath);
  renderGlobalTagsManager();
}
async function updateVideoCatalogTags(workspacePath, tagIds) {
  const data = await api.post('/api/catalog/videos/tags', {
    workspace_path: workspacePath,
    tag_ids: Array.from(new Set(tagIds.map(Number).filter(Boolean))),
    mode: 'replace',
  });
  updateWorkspaceItemCatalogTags(data.workspace_path, data.tags || [], data.item || null);
  return data;
}
async function assignTagToSelectedVideos() {
  const tagId = Number(document.getElementById('tag-manager-assign-tag')?.value || 0);
  if (!tagId) {
    showToast('Выберите тег', 'err');
    return;
  }
  const paths = Array.from(selectedTagManagerVideoPaths);
  if (!paths.length) return;
  try {
    let updated = 0;
    for (const path of paths) {
      const item = tagManagerVideoResults.find(row => row.workspace_path === path) || {};
      const ids = catalogTagIds(item.tags || [], {includeStatus: true});
      if (!ids.includes(tagId)) ids.push(tagId);
      await updateVideoCatalogTags(path, ids);
      updated += 1;
    }
    renderGlobalTagsManager();
    renderWorkspaceListAndDetail();
    showToast(`Тег добавлен к видео: ${updated}`);
  } catch (err) {
    showGlobalTagsError(err.message || 'Не удалось добавить тег к видео');
  }
}
async function removeTagFromSelectedVideos() {
  const tagId = Number(document.getElementById('tag-manager-assign-tag')?.value || 0);
  if (!tagId) {
    showToast('Выберите тег', 'err');
    return;
  }
  const paths = Array.from(selectedTagManagerVideoPaths);
  if (!paths.length) return;
  try {
    let updated = 0;
    for (const path of paths) {
      const item = tagManagerVideoResults.find(row => row.workspace_path === path) || {};
      const ids = catalogTagIds(item.tags || [], {includeStatus: true}).filter(id => id !== tagId);
      await updateVideoCatalogTags(path, ids);
      updated += 1;
    }
    renderGlobalTagsManager();
    renderWorkspaceListAndDetail();
    showToast(`Тег снят с видео: ${updated}`);
  } catch (err) {
    showGlobalTagsError(err.message || 'Не удалось снять тег с видео');
  }
}
async function runStorageProfileTagSync() {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/tag-sync/run`, {});
    if (data.profile) currentStorageProfile = data.profile;
    if (data.items) storageProfileItems = data.items;
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast(`Обновлено по тегам · добавлено: ${data.summary?.added || 0} · найдено: ${data.summary?.matched || 0}`);
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось обновить профиль по тегам');
  }
}
async function linkStorageProfileYoutube() {
  if (!currentStorageProfileId) return;
  const accountId = Number(document.getElementById('storage-profile-youtube-account')?.value || 0);
  if (!accountId) {
    showToast('Сначала выберите YouTube-канал', 'err');
    return;
  }
  try {
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/link`, {account_id: accountId});
    currentStorageProfile = data.profile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    if (data.status === 'linked_with_sync_error') {
      showStorageProfileError(`Канал привязан, но оформление обновить не удалось: ${data.sync_error || 'ошибка sync'}`);
      showToast('Канал привязан, но оформление не обновилось', 'err');
    } else {
      showToast('YouTube-канал привязан к профилю');
    }
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось привязать YouTube-канал');
  }
}
async function unlinkStorageProfileYoutube() {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.del(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/link`);
    currentStorageProfile = data.profile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast('YouTube-канал отвязан');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось отвязать YouTube-канал');
  }
}
function openStorageProfileYoutubeSettings() {
  nav('integrations', document.querySelector('[data-v="integrations"]'));
}
async function syncStorageProfileYoutubeBranding() {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/sync-branding`, {});
    if (data.profile) {
      currentStorageProfile = data.profile;
      storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    }
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    if (data.status === 'failed') {
      showStorageProfileError(data.error || 'Не удалось обновить оформление YouTube');
      showToast('Оформление YouTube не обновлено', 'err');
    } else {
      showToast('Оформление YouTube обновлено');
    }
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось обновить оформление YouTube');
  }
}
async function toggleStorageProfileYoutubeBranding(enabled) {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.patch(`/api/storage-profiles/${Number(currentStorageProfileId)}`, {
      youtube_branding_sync_enabled: Boolean(enabled),
    });
    currentStorageProfile = data.profile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast(enabled ? 'Автооформление YouTube включено' : 'Автооформление YouTube выключено');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось изменить режим оформления YouTube');
  }
}
async function setStorageProfileBrandingOverride(field, enabled) {
  if (!currentStorageProfileId) return;
  const allowed = new Set(['name', 'handle', 'description', 'avatar', 'banner']);
  if (!allowed.has(field)) return;
  try {
    const payload = {};
    payload[`${field}_override`] = Boolean(enabled);
    const data = await api.patch(`/api/storage-profiles/${Number(currentStorageProfileId)}`, payload);
    currentStorageProfile = data.profile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast(enabled ? 'Включено локальное значение поля' : 'Поле снова берётся из YouTube');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось изменить override оформления');
  }
}
async function syncStorageProfileYoutube() {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/sync`, {});
    if (data.profile) currentStorageProfile = data.profile;
    if (data.items) storageProfileItems = data.items;
    storageProfilePublishJobs = data.jobs || storageProfilePublishJobs;
    storageProfileYoutubeVideos = data.youtube_videos || storageProfileYoutubeVideos;
    mergePublishJobsIntoGlobal(storageProfilePublishJobs);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast(`YouTube синхронизирован · найдено: ${data.summary?.fetched || 0} · связано: ${data.summary?.matched_profile_items || 0}`);
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось синхронизировать YouTube для профиля');
  }
}
function selectedStorageProfileItems() {
  return storageProfileItems.filter(item => selectedStorageProfileItemIds.has(Number(item.id)));
}
function toggleStorageProfileItemSelection(itemId, checked) {
  const id = Number(itemId);
  if (checked) selectedStorageProfileItemIds.add(id);
  else selectedStorageProfileItemIds.delete(id);
  renderStorageProfileDetail();
}
function setAllStorageProfileItemSelection(checked) {
  selectedStorageProfileItemIds.clear();
  if (checked) {
    storageProfileItems
      .filter(item => item.file_exists && item.is_publish_ready)
      .forEach(item => selectedStorageProfileItemIds.add(Number(item.id)));
  }
  renderStorageProfileDetail();
}
function mergePublishJobsIntoGlobal(jobs) {
  const byId = new Map(lastPublishJobs.map(job => [Number(job.id), job]));
  (jobs || []).forEach(job => byId.set(Number(job.id), job));
  lastPublishJobs = Array.from(byId.values()).sort((a, b) => Number(b.id) - Number(a.id));
}
function storageProfileLinkedYoutube() {
  return storageProfileYoutubeLink(currentStorageProfile);
}
function storageProfilePublishBadge(item) {
  const job = item.publish_job;
  if (!job) return badge(item.status || 'draft');
  const schedule = job.schedule_state && job.schedule_state !== 'untimed'
    ? `<span class="schedule-state ${esc(job.schedule_state)}">${esc(ruStatus(job.schedule_state))}</span>`
    : '';
  return `${badge(job.status)}${schedule}`;
}
function storageProfilePublishStatus(item) {
  const job = item.publish_job;
  if (!job) return '<div class="mono dim">YouTube: ещё не в очереди</div>';
  const url = job.youtube_url
    ? `<a class="mono" href="${esc(job.youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>`
    : '';
  const error = job.error ? `<button class="link-video err mono" onclick="showPublishJobError(${Number(job.id)})">${esc(shortErrorText(job.error))}</button>` : '';
  const schedule = job.upload_at ? `<div class="mono dim">загрузка: ${esc(formatMoscowDate(job.upload_at))}</div>` : '';
  return `<div class="storage-video-publish-status mono dim">YouTube job #${Number(job.id)} · ${esc(ruStatus(job.status))}${schedule}${url}${error}</div>`;
}
function storageProfilePublishControls() {
  const youtube = storageProfileLinkedYoutube();
  const selected = selectedStorageProfileItems().filter(item => item.file_exists && item.is_publish_ready);
  const selectedCount = selected.length;
  const disabled = !youtube || !selectedCount;
  const publishableItems = storageProfileItems.filter(item => item.file_exists && item.is_publish_ready);
  const allSelected = publishableItems.length && publishableItems.every(item => selectedStorageProfileItemIds.has(Number(item.id)));
  const note = youtube
    ? `Канал: ${esc(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}`
    : 'Сначала привяжите YouTube-канал к профилю.';
  const blocked = storageProfileItems.filter(item => item.file_exists && !item.is_publish_ready).length;
  return `<div class="storage-profile-publish-panel storage-profile-actionbar">
    <div class="storage-actionbar-info">
      <div class="storage-section-title inline-title">Публикация YouTube</div>
      <div class="mono dim">${note}</div>
      <div class="mono dim">${selectedCount ? `Выбрано: ${selectedCount}` : 'Выберите готовые видео для очереди, таймера или загрузки.'}${blocked ? ` · не готовы: ${blocked}` : ''}</div>
    </div>
    <div class="row-actions">
      <button class="btn-secondary" onclick="setAllStorageProfileItemSelection(${allSelected ? 'false' : 'true'})">${allSelected ? 'Снять выбор' : 'Выбрать все'}</button>
      <button class="btn-secondary" ${disabled ? 'disabled' : ''} onclick="enqueueStorageProfileSelection('queue')">В очередь</button>
      <button class="btn-secondary" ${disabled ? 'disabled' : ''} onclick="enqueueStorageProfileSelection('schedule')">Таймер</button>
      <button class="btn-primary" ${disabled ? 'disabled' : ''} onclick="enqueueStorageProfileSelection('run')">Загрузить сейчас</button>
      <button class="btn-secondary" ${youtube ? '' : 'disabled'} onclick="syncStorageProfileYoutube()">Синхронизировать YouTube</button>
      <button class="btn-mini" onclick="loadStorageProfileDetail(${Number(currentStorageProfileId)})">Обновить</button>
      <button class="btn-mini" onclick="openStorageProfileDrawer('publish')">Настройки</button>
    </div>
  </div>`;
}
function storageProfilePublishSettings() {
  const link = currentStorageProfile?.service_links?.find(item => item.platform === 'youtube') || null;
  let raw = {};
  try {
    raw = link?.settings_json ? JSON.parse(link.settings_json) : {};
  } catch {
    raw = {};
  }
  const settings = raw.publish || raw || {};
  return {
    publish_mode: settings.publish_mode || 'public',
    category_id: settings.category_id || '22',
    made_for_kids: Boolean(settings.made_for_kids),
    title_template: settings.title_template || '',
    description_template: settings.description_template || '',
    tags_template: settings.tags_template || '',
    default_action: settings.default_action || 'queue',
  };
}
function storageProfilePublishSettingsPanel() {
  const settings = storageProfilePublishSettings();
  return `<div class="storage-profile-publish-settings">
    <div class="storage-tag-panel-head">
      <div>
        <div class="storage-section-title inline-title">Настройки публикации профиля</div>
        <div class="mono dim">Эти defaults применяются только к этому локальному профилю. Очередь и таймер доступны ниже.</div>
      </div>
      <button class="btn-secondary" onclick="saveStorageProfilePublishSettings()">Сохранить настройки публикации</button>
    </div>
    <div class="field-grid">
      <div class="field">
        <label class="field-lbl">Видимость YouTube</label>
        <select id="storage-publish-mode">
          <option value="private"${settings.publish_mode === 'private' ? ' selected' : ''}>private · приватно</option>
          <option value="unlisted"${settings.publish_mode === 'unlisted' ? ' selected' : ''}>unlisted · по ссылке</option>
          <option value="public"${settings.publish_mode === 'public' ? ' selected' : ''}>public · публично</option>
        </select>
      </div>
      <div class="field">
        <label class="field-lbl">ID категории</label>
        <input id="storage-publish-category" type="text" value="${esc(settings.category_id || '22')}">
      </div>
      <div class="field">
        <label class="field-lbl">Действие по умолчанию</label>
        <select id="storage-publish-default-action">
          <option value="queue"${settings.default_action === 'queue' ? ' selected' : ''}>В очередь</option>
          <option value="schedule"${settings.default_action === 'schedule' ? ' selected' : ''}>Таймер</option>
          <option value="run"${settings.default_action === 'run' ? ' selected' : ''}>Загрузить сейчас</option>
        </select>
      </div>
      <label class="toggle-label storage-publish-kids"><input id="storage-publish-made-for-kids" type="checkbox" ${settings.made_for_kids ? 'checked' : ''}> Для детей</label>
    </div>
    <div class="field-grid">
      <div class="field"><label class="field-lbl">Шаблон title</label><input id="storage-publish-title-template" type="text" value="${esc(settings.title_template)}" placeholder="{title}"></div>
      <div class="field"><label class="field-lbl">Шаблон tags</label><input id="storage-publish-tags-template" type="text" value="${esc(settings.tags_template)}" placeholder="shorts, {profile}"></div>
    </div>
    <div class="field">
      <label class="field-lbl">Шаблон description</label>
      <textarea id="storage-publish-description-template" rows="3" placeholder="Описание для роликов этого профиля">${esc(settings.description_template)}</textarea>
    </div>
    <div class="mono dim">Доступные переменные: {title}, {file_name}, {stem}, {path}, {profile}, {handle}.</div>
  </div>`;
}
function readStorageProfilePublishSettingsForm() {
  const current = storageProfilePublishSettings();
  return {
    publish_mode: document.getElementById('storage-publish-mode')?.value || current.publish_mode || 'public',
    category_id: document.getElementById('storage-publish-category')?.value || current.category_id || '22',
    made_for_kids: document.getElementById('storage-publish-made-for-kids')
      ? Boolean(document.getElementById('storage-publish-made-for-kids')?.checked)
      : Boolean(current.made_for_kids),
    title_template: document.getElementById('storage-publish-title-template')?.value ?? current.title_template ?? '',
    description_template: document.getElementById('storage-publish-description-template')?.value ?? current.description_template ?? '',
    tags_template: document.getElementById('storage-publish-tags-template')?.value ?? current.tags_template ?? '',
    default_action: document.getElementById('storage-publish-default-action')?.value || current.default_action || 'queue',
  };
}
async function saveStorageProfilePublishSettings() {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.patch(`/api/storage-profiles/${Number(currentStorageProfileId)}/publish-settings`, readStorageProfilePublishSettingsForm());
    currentStorageProfile = data.profile || currentStorageProfile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(currentStorageProfile.id) ? currentStorageProfile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast('Настройки публикации профиля сохранены');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось сохранить настройки публикации профиля');
  }
}
function storageProfileChannelProfile(profile = currentStorageProfile) {
  const youtube = storageProfileYoutubeLink(profile);
  const accountId = Number(youtube?.external_account_id || youtube?.youtube_account?.id || 0);
  const effectiveName = String(storageProfileName(profile) || '').trim().toLowerCase();
  const handle = String(storageProfileHandle(profile) || '').trim().toLowerCase();
  return editingProfiles.find(item =>
    accountId && Number(item.youtube_account_id || 0) === accountId
  ) || editingProfiles.find(item => {
    const name = String(item.name || '').trim().toLowerCase();
    return name && (name === effectiveName || name === handle);
  }) || null;
}
function storageProfileChannelSettingsPanel(profile = currentStorageProfile) {
  const channelProfile = storageProfileChannelProfile(profile);
  const youtube = storageProfileYoutubeLink(profile);
  const linkedAccountId = Number(youtube?.external_account_id || youtube?.youtube_account?.id || 0);
  const selectedAccountId = Number(channelProfile?.youtube_account_id || linkedAccountId || storageYoutubeAccounts[0]?.id || 0);
  const selectedTemplateId = Number(channelProfile?.default_studio_template_id || 0);
  const selectedPoolId = Number(channelProfile?.reaction_pool_id || 0);
  const accountRows = (storageYoutubeAccounts.length ? storageYoutubeAccounts : editingAccounts).filter(account => (account.status || 'active') === 'active');
  const accountOptions = accountRows.map(account => `<option value="${Number(account.id)}"${Number(account.id) === selectedAccountId ? ' selected' : ''}>${esc(storageAccountTitle(account))}</option>`).join('');
  const templateOptions = activeStudioEditingTemplates().map(item => {
    const id = Number(item.studio_template_id || item.id);
    return `<option value="${id}"${id === selectedTemplateId ? ' selected' : ''}>${esc(item.name || item.key)} · ${esc(item.key || '')} v${Number(item.version || 1)}</option>`;
  }).join('');
  const poolOptions = editingPools.map(pool => `<option value="${Number(pool.id)}"${Number(pool.id) === selectedPoolId ? ' selected' : ''}>${esc(pool.name)}${pool.item_count ? ` · ${Number(pool.item_count)}` : ''}</option>`).join('');
  const title = channelProfile
    ? `Channel profile #${Number(channelProfile.id)}`
    : 'Channel profile будет создан при сохранении';
  const disabledHint = channelProfile?.enabled === false
    ? '<div class="mono warn">Этот channel profile сейчас отключён. Включите его, чтобы новые render-задачи могли использовать настройки.</div>'
    : '';
  return `<div class="storage-channel-settings compact-panel">
    <div class="storage-tag-panel-head">
      <div>
        <div class="storage-section-title inline-title">Обработка канала</div>
        <div class="mono dim">${esc(title)} · Studio template, пул реакций и publish defaults для render-задач этого профиля.</div>
      </div>
      <div class="row-actions">
        ${selectedTemplateId ? `<button class="btn-mini" onclick="openStudioTemplate(${selectedTemplateId})">Редактировать шаблон</button>` : ''}
        <button class="btn-mini" onclick="openQueueForChannelProfile()">Открыть render-очередь</button>
      </div>
    </div>
    ${disabledHint}
    <div class="field-grid">
      <div class="field"><label class="field-lbl">Название channel profile</label><input id="storage-channel-profile-name" type="text" value="${esc(channelProfile?.name || storageProfileName(profile) || '')}"></div>
      <div class="field"><label class="field-lbl">YouTube аккаунт</label><select id="storage-channel-profile-account"><option value="">Без YouTube</option>${accountOptions}</select></div>
      <div class="field"><label class="field-lbl">Studio template по умолчанию</label><select id="storage-channel-profile-template"><option value="">Не выбран</option>${templateOptions}</select></div>
      <div class="field"><label class="field-lbl">Пул реакций</label><select id="storage-channel-profile-pool"><option value="">Без пула реакций</option>${poolOptions}</select></div>
      <div class="field"><label class="field-lbl">Видимость по умолчанию</label><select id="storage-channel-profile-privacy">
        <option value=""${!channelProfile?.default_privacy ? ' selected' : ''}>из настроек публикации</option>
        <option value="public"${channelProfile?.default_privacy === 'public' ? ' selected' : ''}>public</option>
        <option value="unlisted"${channelProfile?.default_privacy === 'unlisted' ? ' selected' : ''}>unlisted</option>
        <option value="private"${channelProfile?.default_privacy === 'private' ? ' selected' : ''}>private</option>
      </select></div>
      <div class="field"><label class="field-lbl">ID категории</label><input id="storage-channel-profile-category" type="text" value="${esc(channelProfile?.default_category_id || '')}" placeholder="22"></div>
    </div>
    <div class="field-grid">
      <div class="field"><label class="field-lbl">Шаблон title</label><input id="storage-channel-profile-title-template" type="text" value="${esc(channelProfile?.title_template || '')}" placeholder="{title}"></div>
      <div class="field"><label class="field-lbl">Шаблон tags</label><input id="storage-channel-profile-tags-template" type="text" value="${esc(channelProfile?.tags_template || '')}" placeholder="shorts, {profile}"></div>
    </div>
    <div class="field"><label class="field-lbl">Шаблон description</label><textarea id="storage-channel-profile-description-template" rows="3">${esc(channelProfile?.description_template || '')}</textarea></div>
    <label class="toggle-label"><input id="storage-channel-profile-enabled" type="checkbox" ${channelProfile?.enabled === false ? '' : 'checked'}> Channel profile включён</label>
  </div>`;
}
function readStorageProfileChannelSettingsForm() {
  return {
    name: document.getElementById('storage-channel-profile-name')?.value.trim() || storageProfileName(currentStorageProfile) || 'Channel profile',
    youtube_account_id: editingOptionalId(document.getElementById('storage-channel-profile-account')?.value),
    default_studio_template_id: editingOptionalId(document.getElementById('storage-channel-profile-template')?.value),
    reaction_pool_id: editingOptionalId(document.getElementById('storage-channel-profile-pool')?.value),
    title_template: document.getElementById('storage-channel-profile-title-template')?.value || null,
    description_template: document.getElementById('storage-channel-profile-description-template')?.value || null,
    tags_template: document.getElementById('storage-channel-profile-tags-template')?.value || null,
    default_privacy: document.getElementById('storage-channel-profile-privacy')?.value || null,
    default_category_id: document.getElementById('storage-channel-profile-category')?.value.trim() || null,
    enabled: Boolean(document.getElementById('storage-channel-profile-enabled')?.checked),
  };
}
async function saveStorageProfileChannelSettings() {
  if (!currentStorageProfileId) return;
  try {
    const existing = storageProfileChannelProfile();
    const body = readStorageProfileChannelSettingsForm();
    const data = existing
      ? await api.patch(`/api/editing/channel-profiles/${Number(existing.id)}`, body)
      : await api.post('/api/editing/channel-profiles', body);
    const item = data.item;
    if (item) {
      const id = Number(item.id);
      editingProfiles = editingProfiles.filter(profile => Number(profile.id) !== id).concat([item]);
    }
    showToast(existing ? 'Настройки обработки профиля сохранены' : 'Channel profile создан');
    renderStorageProfileDetail();
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось сохранить настройки обработки профиля');
  }
}
async function openQueueForChannelProfile() {
  const profile = storageProfileChannelProfile();
  const query = profile?.name || storageProfileName(currentStorageProfile) || '';
  queueSearchQuery = query;
  const input = document.getElementById('queue-search-input');
  if (input) input.value = query;
  nav('queue', document.querySelector('[data-v="queue"]'));
  setQueueQuickFilter('render');
}
function storageProfilePublishJobsPanel() {
  if (!storageProfilePublishJobs.length) {
    return '<div class="storage-profile-jobs empty">Задач публикации для этого профиля пока нет.</div>';
  }
  const rows = storageProfilePublishJobs.slice(0, 80).map(job => {
    const youtubeLink = job.youtube_url ? `<a class="btn-mini" href="${esc(job.youtube_url)}" target="_blank" rel="noopener noreferrer">YouTube</a>` : '—';
    const actions = [];
    if (job.can_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)})">Запустить</button>`);
    else if (job.can_force_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)}, true)">Запустить сейчас</button>`);
    if (job.can_retry) actions.push(`<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Повторить</button>`);
    if (job.can_cancel) actions.push(`<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Отменить</button>`);
    if (job.status === 'queued') actions.push(`<button class="btn-secondary" onclick="openStorageProfileScheduleForJobs([${Number(job.id)}])">Таймер</button>`);
    return `<tr><td class="mono dim">#${job.id}</td><td>${badge(job.status)}</td><td class="mono mid ov">${esc(job.title || '—')}</td><td>${renderPublishScheduleCell(job)}</td><td class="mono dim ov">${esc(shortPath(job.clip_output_path || '—'))}</td><td>${youtubeLink}</td><td><div class="row-actions">${actions.join('')}</div></td></tr>`;
  }).join('');
  return `<div class="storage-profile-jobs"><table class="tbl compact"><thead><tr><th>#</th><th>Статус</th><th>Видео</th><th>Таймер</th><th>Файл</th><th>YouTube</th><th>Действие</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function storageProfileYoutubeVideoCard(video) {
  const thumb = video.thumbnail_url
    ? `<img src="${esc(video.thumbnail_url)}" alt="">`
    : `<div class="video-thumb-placeholder"><i class="ti ti-brand-youtube"></i></div>`;
  const title = video.title || video.external_video_id || 'YouTube video';
  const matched = video.matched
    ? '<span class="badge b-ok">связано с профилем</span>'
    : '<span class="badge">только на YouTube</span>';
  const privacy = video.privacy_status ? badge(video.privacy_status) : '';
  const published = video.published_at ? `<div class="mono dim">опубликовано: ${esc(formatMtime(video.published_at))}</div>` : '';
  const local = video.profile_item_workspace_path ? `<div class="mono dim ov">${esc(workspaceDisplayPath(video.profile_item_workspace_path))}</div>` : '';
  return `<article class="storage-youtube-video-card">
    <div class="storage-youtube-thumb">${thumb}</div>
    <div class="storage-video-title" title="${esc(title)}">${esc(title)}</div>
    <div class="storage-video-meta">${matched}${privacy}</div>
    ${published}
    ${local}
    <div class="row-actions">
      <a class="btn-mini" href="${esc(video.external_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>
    </div>
  </article>`;
}
function storageProfileYoutubeVideosPanel() {
  const youtube = storageProfileLinkedYoutube();
  const link = currentStorageProfile?.service_links?.find(item => item.platform === 'youtube');
  const syncInfo = link?.last_sync_at
    ? `Последняя синхронизация YouTube: ${formatMtime(link.last_sync_at)} · найдено видео: ${Number(link.synced_video_count || storageProfileYoutubeVideos.length || 0)}`
    : 'Полной синхронизации с YouTube ещё не было.';
  const error = link?.last_sync_error ? `<div class="mono err">Ошибка sync: ${esc(link.last_sync_error)}</div>` : '';
  if (!youtube) {
    return '<div class="storage-youtube-inventory empty">Привяжите YouTube-канал, чтобы сверить профиль с реальным каналом.</div>';
  }
  const body = storageProfileYoutubeVideos.length
    ? `<div class="storage-youtube-grid">${storageProfileYoutubeVideos.map(storageProfileYoutubeVideoCard).join('')}</div>`
    : '<div class="empty">Видео YouTube ещё не загружены. Нажмите «Синхронизировать YouTube».</div>';
  return `<div class="storage-youtube-inventory">
    <div class="mono dim">${esc(syncInfo)}</div>
    ${error}
    ${body}
  </div>`;
}
async function refreshStorageProfilePublishState() {
  if (!currentStorageProfileId) return;
  const [profileData, jobsData, youtubeVideosData] = await Promise.all([
    api.get(`/api/storage-profiles/${Number(currentStorageProfileId)}`),
    api.get(`/api/storage-profiles/${Number(currentStorageProfileId)}/publish-jobs?limit=200`),
    api.get(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/videos?limit=200`).catch(() => ({videos: []})),
  ]);
  currentStorageProfile = profileData.profile;
  storageProfileItems = profileData.items || [];
  storageProfilePublishJobs = jobsData.jobs || [];
  storageProfileYoutubeVideos = youtubeVideosData.videos || [];
  mergePublishJobsIntoGlobal(storageProfilePublishJobs);
  selectedStorageProfileItemIds = new Set(Array.from(selectedStorageProfileItemIds).filter(id => storageProfileItems.some(item => Number(item.id) === Number(id))));
  renderStorageProfilesGrid();
  renderStorageProfileDetail();
}
async function enqueueStorageProfileSelection(mode = 'queue') {
  if (!currentStorageProfileId) return;
  const publishSettings = readStorageProfilePublishSettingsForm();
  const selected = selectedStorageProfileItems().filter(item => item.file_exists && item.is_publish_ready);
  if (!selected.length) {
    showToast('Выберите видео профиля с тегом «Готово»', 'err');
    return;
  }
  try {
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/youtube/enqueue`, {
      item_ids: selected.map(item => Number(item.id)),
      publish_mode: publishSettings.publish_mode,
      category_id: publishSettings.category_id,
      made_for_kids: publishSettings.made_for_kids,
      title_template: publishSettings.title_template,
      description_template: publishSettings.description_template,
      tags_template: publishSettings.tags_template,
    });
    if (data.profile) currentStorageProfile = data.profile;
    if (data.profile_items) storageProfileItems = data.profile_items;
    mergePublishJobsIntoGlobal(data.jobs || []);
    await refreshStorageProfilePublishState();
    const jobs = (data.jobs || []).filter(job => job.status === 'queued' || job.status === 'failed');
    if (mode === 'schedule') {
      await openStorageProfileScheduleForJobs(jobs.map(job => Number(job.id)));
    } else if (mode === 'run') {
      await runStorageProfilePublishJobsNow(jobs.map(job => Number(job.id)));
    } else {
      showToast(`В очереди YouTube: создано ${data.summary?.created || 0}, обновлено ${data.summary?.updated || 0}`);
    }
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось добавить видео профиля в YouTube-очередь');
  }
}
async function openStorageProfileScheduleForJobs(jobIds) {
  const ids = (jobIds || []).map(Number).filter(Boolean);
  if (!ids.length) {
    showToast('Нет queued jobs для таймера', 'err');
    return;
  }
  try {
    const groupsData = await api.get('/api/publish/schedule-groups');
    lastPublishScheduleGroups = groupsData.groups || [];
  } catch {}
  mergePublishJobsIntoGlobal(storageProfilePublishJobs);
  selectedPublishJobIds = new Set(ids);
  openPublishScheduleEditor(null);
}
async function runStorageProfilePublishJobsNow(jobIds) {
  const ids = (jobIds || []).map(Number).filter(Boolean);
  if (!ids.length) {
    showToast('Нет задач для запуска', 'err');
    return;
  }
  if (!confirm(`Загрузить выбранные видео в YouTube сейчас: ${ids.length}?`)) return;
  try {
    const data = await api.post('/api/publish/jobs/bulk-run', {job_ids: ids, force: true});
    showToast(`Запущено: ${data.summary?.processed || 0} · ошибок: ${data.summary?.errors || 0}`);
    await refreshStorageProfilePublishState();
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось запустить загрузку из профиля');
  }
}
function storageProfileItemCard(item) {
  const title = item.title || item.file_name || item.workspace_path;
  const missing = !item.file_exists;
  const selected = selectedStorageProfileItemIds.has(Number(item.id));
  const ready = Boolean(item.is_publish_ready);
  const publishNote = ready
    ? ''
    : '<div class="mono warn storage-video-publish-status">YouTube: нужен тег «Готово»</div>';
  return `<article class="storage-video-card${missing ? ' missing' : ''}">
    <label class="storage-video-select"><input type="checkbox" ${selected ? 'checked' : ''} ${missing || !ready ? 'disabled' : ''} onchange="toggleStorageProfileItemSelection(${Number(item.id)}, this.checked)"> <span>Выбрать для YouTube</span></label>
    <div class="storage-video-thumb">${missing ? videoThumb(item.workspace_path, title) : videoWatchThumb(item.workspace_path, title)}</div>
    <div class="storage-video-title" title="${esc(title)}">${esc(title)}</div>
    <div class="mono dim storage-video-path" title="${esc(item.workspace_path)}">${esc(workspaceDisplayPath(item.workspace_path))}</div>
    ${tagListPills(item.catalog_tags || [])}
    <div class="storage-video-meta">
      <span>${missing ? badge('failed') : storageProfilePublishBadge(item)}</span>
      <span class="mono dim">${esc(workspaceFolderLabel(item.section || ''))}</span>
    </div>
    ${publishNote}
    ${storageProfilePublishStatus(item)}
    <div class="row-actions">
      ${missing ? `<button class="btn-mini" disabled title="${esc(item.path_error || 'Файл отсутствует')}">Смотреть</button>` : webPlayerButton(item.workspace_path, 'Смотреть')}
      <button class="btn-danger" onclick="removeStorageProfileItem(${Number(item.id)})">Убрать</button>
    </div>
  </article>`;
}
function renderStorageCandidatePicker() {
  if (!storageCandidatePickerOpen) return '';
  const existing = new Set(storageProfileItems.map(item => item.workspace_path));
  const candidates = storageProfileCandidates.filter(item => !existing.has(item.workspace_path));
  const selectedCount = Array.from(selectedStorageCandidatePaths).filter(path => candidates.some(item => item.workspace_path === path)).length;
  const body = candidates.length
    ? `<div class="storage-candidate-list">${candidates.map(item => {
        const checked = selectedStorageCandidatePaths.has(item.workspace_path);
        return `<div class="storage-candidate-row">
        <label class="storage-video-select"><input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleStorageCandidateSelection('${esc(item.workspace_path)}', this.checked)"></label>
        ${videoWatchThumb(item.workspace_path, item.title || item.file_name)}
        <div style="min-width:0;flex:1">
          <div class="mono txt ov">${esc(item.title || item.file_name)}</div>
          <div class="mono dim ov">${esc(workspaceDisplayPath(item.workspace_path))}</div>
          ${tagListPills(item.tags || [])}
        </div>
        <button class="btn-secondary" data-path="${esc(item.workspace_path)}" onclick="addCandidateToStorageProfile(this.dataset.path)">Добавить</button>
      </div>`;
      }).join('')}</div>`
    : '<div class="empty">Начните писать название, путь или тег — результаты появятся автоматически. Можно нажать «Случайные видео».</div>';
  return `<div class="storage-candidates">
    <div class="box-head"><span>Добавить видео из каталога</span><button class="btn-mini" onclick="toggleStorageCandidatePicker()">Скрыть</button></div>
    <div class="storage-search-panel">
      <input id="storage-video-search-input" type="text" placeholder="Искать по названию, тегу или пути…" value="${esc(storageCatalogSearchQuery || '')}" oninput="onStorageCatalogSearchInput(this.value)" autofocus>
      <button class="btn-secondary" onclick="loadRandomStorageCatalogVideos()">Случайные видео</button>
      <button class="btn-primary" ${selectedCount ? '' : 'disabled'} onclick="addSelectedCatalogVideosToStorageProfile()">Добавить выбранные (${selectedCount})</button>
    </div>
    ${body}
  </div>`;
}
function storageProfileStats() {
  const total = storageProfileItems.length;
  const ready = storageProfileItems.filter(item => item.file_exists && item.is_publish_ready).length;
  const blocked = storageProfileItems.filter(item => item.file_exists && !item.is_publish_ready).length;
  const missing = storageProfileItems.filter(item => !item.file_exists).length;
  const queued = storageProfilePublishJobs.filter(job => ['queued', 'scheduled'].includes(job.status)).length;
  const failed = storageProfilePublishJobs.filter(job => ['failed', 'cancelled'].includes(job.status)).length;
  const done = storageProfilePublishJobs.filter(job => job.status === 'done').length;
  return {total, ready, blocked, missing, queued, failed, done, selected: selectedStorageProfileItems().length};
}
function storageProfileMetric(label, value, tone = '') {
  return `<div class="storage-profile-metric ${esc(tone)}"><b>${Number(value) || 0}</b><span>${esc(label)}</span></div>`;
}
function storageProfileTabs() {
  const stats = storageProfileStats();
  return `<div class="storage-profile-tabs">
    ${storageProfileTabButton('publish', 'Публикация', stats.ready)}
    ${storageProfileTabButton('videos', 'Видео', stats.total)}
    ${storageProfileTabButton('queue', 'Очередь', storageProfilePublishJobs.length)}
    ${storageProfileTabButton('youtube', 'YouTube', storageProfileYoutubeVideos.length)}
    ${storageProfileTabButton('errors', 'Ошибки', stats.failed + stats.missing)}
  </div>`;
}
function storageProfileVideoGrid(items, emptyText) {
  return items.length
    ? `<div class="storage-video-grid storage-video-grid-compact">${items.map(storageProfileItemCard).join('')}</div>`
    : `<div class="empty compact">${esc(emptyText)}</div>`;
}
function storageProfilePublishDashboard(profile) {
  const stats = storageProfileStats();
  const ready = storageProfileItems.filter(item => item.file_exists && item.is_publish_ready);
  const waiting = storageProfileItems.filter(item => item.file_exists && !item.is_publish_ready);
  const youtube = storageProfileLinkedYoutube();
  const spotlight = ready.length ? ready : storageProfileItems.filter(item => item.file_exists).slice(0, 18);
  return `<div class="storage-profile-main-grid">
    <section class="storage-profile-cockpit">
      <div class="storage-cockpit-head">
        <div>
          <div class="storage-section-title inline-title">Готовность к публикации</div>
          <div class="mono dim">${youtube ? `Канал: ${esc(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}` : 'YouTube-канал ещё не привязан.'}</div>
        </div>
        <button class="btn-mini" onclick="openStorageProfileDrawer('tags')">Теги профиля</button>
      </div>
      <div class="storage-profile-metrics">
        ${storageProfileMetric('видео', stats.total)}
        ${storageProfileMetric('готово', stats.ready, 'ok')}
        ${storageProfileMetric('ждут теги', stats.blocked, 'warn')}
        ${storageProfileMetric('в очереди', stats.queued)}
        ${storageProfileMetric('ошибки', stats.failed + stats.missing, 'err')}
      </div>
      ${waiting.length ? `<div class="mono warn">Без тега «Готово» не публикуются: ${waiting.length}</div>` : '<div class="mono dim">Все доступные видео готовы к публикации или уже обработаны.</div>'}
    </section>
    <section class="storage-profile-feed">
      <div class="storage-feed-head">
        <div>
          <div class="storage-section-title inline-title">Видео для публикации</div>
          <div class="mono dim">Выбирайте карточки и отправляйте их в очередь, на таймер или сразу в YouTube.</div>
        </div>
        <button class="btn-secondary" onclick="openStorageProfileVideoPicker()">Добавить видео</button>
      </div>
      ${storageProfileVideoGrid(spotlight, 'В профиле пока нет готовых видео. Добавьте ролики через поиск по каталогу.')}
    </section>
  </div>`;
}
function storageProfileErrorsPanel() {
  const missing = storageProfileItems.filter(item => !item.file_exists);
  const failedJobs = storageProfilePublishJobs.filter(job => ['failed', 'cancelled'].includes(job.status));
  const brandingError = currentStorageProfile?.youtube_branding?.sync_error || '';
  const link = currentStorageProfile?.service_links?.find(item => item.platform === 'youtube');
  const syncError = link?.last_sync_error || '';
  const jobRows = failedJobs.length
    ? `<div class="storage-profile-jobs"><table class="tbl compact"><thead><tr><th>#</th><th>Статус</th><th>Видео</th><th>Ошибка</th><th>Действие</th></tr></thead><tbody>${failedJobs.map(job => `<tr><td class="mono dim">#${Number(job.id)}</td><td>${badge(job.status)}</td><td class="mono ov">${esc(job.title || '—')}</td><td class="mono err ov">${esc(shortErrorText(job.error || '—'))}</td><td><div class="row-actions">${job.can_retry ? `<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Повторить</button>` : ''}${job.can_cancel ? `<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Отменить</button>` : ''}</div></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty compact">Ошибок публикации нет.</div>';
  return `<div class="storage-profile-errors">
    ${brandingError ? `<div class="storage-error-card"><b>Оформление YouTube</b><span>${esc(shortErrorText(brandingError))}</span><button class="btn-mini" onclick="syncStorageProfileYoutubeBranding()">Повторить sync</button></div>` : ''}
    ${syncError ? `<div class="storage-error-card"><b>Синхронизация YouTube</b><span>${esc(shortErrorText(syncError))}</span><button class="btn-mini" onclick="syncStorageProfileYoutube()">Повторить sync</button></div>` : ''}
    ${missing.length ? `<div class="storage-section-title">Файлы отсутствуют</div>${storageProfileVideoGrid(missing, '')}` : ''}
    <div class="storage-section-title">Ошибки очереди</div>
    ${jobRows}
  </div>`;
}
function storageProfileMainContent(profile) {
  if (storageProfileActiveTab === 'videos') {
    return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Все видео профиля</div><div class="mono dim">Локальная витрина профиля. Публикация доступна только для видео с тегом «Готово».</div></div><button class="btn-secondary" onclick="openStorageProfileVideoPicker()">Добавить видео</button></div>${storageProfileVideoGrid(storageProfileItems, 'В профиле пока нет видео.')}`;
  }
  if (storageProfileActiveTab === 'queue') {
    return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Очередь публикации профиля</div><div class="mono dim">Здесь видны задачи YouTube только этого профиля.</div></div><button class="btn-mini" onclick="refreshStorageProfilePublishState()">Обновить</button></div>${storageProfilePublishJobsPanel()}`;
  }
  if (storageProfileActiveTab === 'youtube') {
    return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Видео на YouTube</div><div class="mono dim">Сверка локального профиля с подключённым каналом.</div></div><button class="btn-secondary" onclick="syncStorageProfileYoutube()">Синхронизировать YouTube</button></div>${storageProfileYoutubeVideosPanel()}`;
  }
  if (storageProfileActiveTab === 'errors') {
    return storageProfileErrorsPanel();
  }
  return storageProfilePublishDashboard(profile);
}
function storageProfileLocalEditPanel(profile) {
  return `<div class="storage-profile-edit compact-panel">
    <div class="settings-meta">Локальные значения работают как ручное переопределение оформления YouTube только для изменённых полей.</div>
    <div class="field-grid">
      <div class="field"><label class="field-lbl">Локальное название</label><input id="storage-profile-name" type="text" value="${esc(profile.name)}">${storageBrandingFieldActions(profile, 'name', 'Использовать локальное имя', 'Вернуть имя из YouTube')}</div>
      <div class="field"><label class="field-lbl">Локальный Handle</label><input id="storage-profile-handle" type="text" value="${esc(profile.handle)}">${storageBrandingFieldActions(profile, 'handle', 'Использовать локальный handle', 'Вернуть handle из YouTube')}</div>
    </div>
    <div class="field"><label class="field-lbl">Локальное описание</label><textarea id="storage-profile-description" rows="3">${esc(profile.description || '')}</textarea>${storageBrandingFieldActions(profile, 'description', 'Использовать локальное описание', 'Вернуть описание из YouTube')}</div>
    <div class="field-grid">
      <div class="field"><label class="field-lbl">Инициалы</label><input id="storage-profile-initials" type="text" value="${esc(profile.avatar_initials || '')}"></div>
      <div class="field"><label class="field-lbl">Цвет аватара</label><input id="storage-profile-avatar-color" type="text" value="${esc(profile.avatar_color || '#3b82f6')}"></div>
      <div class="field"><label class="field-lbl">URL локального аватара</label><input id="storage-profile-avatar-url" type="text" value="${esc(profile.avatar_url || '')}">${storageBrandingFieldActions(profile, 'avatar', 'Использовать локальный аватар', 'Вернуть фото YouTube')}</div>
      <div class="field"><label class="field-lbl">Цвет баннера</label><input id="storage-profile-banner-color" type="text" value="${esc(profile.banner_color || '#111827')}"></div>
      <div class="field"><label class="field-lbl">URL локальной шапки</label><input id="storage-profile-banner-url" type="text" value="${esc(profile.banner_url || '')}">${storageBrandingFieldActions(profile, 'banner', 'Использовать локальную шапку', 'Вернуть шапку из YouTube')}</div>
    </div>
  </div>`;
}
function storageProfileDrawerButton(section, label) {
  const handler = section === 'add-video'
    ? 'openStorageProfileVideoPicker()'
    : `openStorageProfileDrawer('${esc(section)}')`;
  return `<button class="${storageProfileDrawerSection === section ? 'active' : ''}" onclick="${handler}">${esc(label)}</button>`;
}
function storageProfileDrawerTitle(section) {
  const titles = {
    publish: 'Настройки публикации',
    processing: 'Обработка канала',
    tags: 'Теги профиля',
    youtube: 'YouTube и оформление',
    profile: 'Локальные данные',
    danger: 'Опасная зона',
    'add-video': 'Добавить видео',
  };
  return titles[section] || 'Настройки';
}
function storageProfileDrawerBody(profile) {
  if (storageProfileDrawerSection === 'processing') return storageProfileChannelSettingsPanel(profile);
  if (storageProfileDrawerSection === 'tags') return storageProfileTagRulesPanel(profile);
  if (storageProfileDrawerSection === 'youtube') return storageProfileServiceLinks(profile);
  if (storageProfileDrawerSection === 'profile') return storageProfileLocalEditPanel(profile);
  if (storageProfileDrawerSection === 'danger') {
    return `<div class="storage-danger-panel"><b>Отключить профиль</b><p class="mono dim">Профиль исчезнет из списка, видео на диске не удаляются.</p><button class="btn-danger" onclick="disableStorageProfile()">Отключить профиль</button></div>`;
  }
  if (storageProfileDrawerSection === 'add-video') return renderStorageCandidatePicker();
  return storageProfilePublishSettingsPanel();
}
function storageProfileDrawerFooter() {
  if (storageProfileDrawerSection === 'publish') {
    return `<button class="btn-primary" onclick="saveStorageProfilePublishSettings()">Сохранить публикацию</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
  }
  if (storageProfileDrawerSection === 'processing') {
    return `<button class="btn-primary" onclick="saveStorageProfileChannelSettings()">Сохранить обработку</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
  }
  if (storageProfileDrawerSection === 'profile') {
    return `<button class="btn-primary" onclick="saveStorageProfile()">Сохранить профиль</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
  }
  return `<button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
}
function renderStorageProfileDrawer(profile) {
  if (!storageProfileDrawerOpen) return '';
  return `<div class="storage-profile-drawer-backdrop" onclick="closeStorageProfileDrawer()"></div>
  <aside class="storage-profile-drawer">
    <div class="storage-drawer-head">
      <div><div class="inspector-kicker">Профиль · настройки</div><h2>${esc(storageProfileDrawerTitle(storageProfileDrawerSection))}</h2></div>
      <button class="btn-mini" onclick="closeStorageProfileDrawer()">×</button>
    </div>
    <div class="storage-drawer-nav">
      ${storageProfileDrawerButton('publish', 'Публикация')}
      ${storageProfileDrawerButton('processing', 'Обработка')}
      ${storageProfileDrawerButton('tags', 'Теги')}
      ${storageProfileDrawerButton('youtube', 'YouTube')}
      ${storageProfileDrawerButton('profile', 'Профиль')}
      ${storageProfileDrawerButton('add-video', 'Добавить видео')}
      ${storageProfileDrawerButton('danger', 'Опасное')}
    </div>
    <div class="storage-drawer-body">${storageProfileDrawerBody(profile)}</div>
    <div class="storage-drawer-footer">${storageProfileDrawerFooter()}</div>
  </aside>`;
}
function renderStorageProfileDetail() {
  const el = document.getElementById('storage-profile-detail');
  if (!el) return;
  const profile = currentStorageProfile;
  if (!profile) {
    const title = document.getElementById('storage-profile-view-title');
    const subtitle = document.getElementById('storage-profile-view-subtitle');
    const headTitle = document.getElementById('storage-profile-page-head-title');
    if (title) title.textContent = 'Профиль';
    if (subtitle) subtitle.textContent = 'отдельная страница локального канала';
    if (headTitle) headTitle.textContent = 'Витрина профиля';
    el.innerHTML = '<div class="empty">Создайте профиль кнопкой «+» или выберите существующий.</div>';
    return;
  }
  const title = document.getElementById('storage-profile-view-title');
  const subtitle = document.getElementById('storage-profile-view-subtitle');
  const headTitle = document.getElementById('storage-profile-page-head-title');
  const effectiveName = storageProfileName(profile);
  const effectiveHandle = storageProfileHandle(profile);
  const effectiveDescription = storageProfileDescription(profile);
  if (title) title.textContent = effectiveName || 'Профиль';
  if (subtitle) subtitle.textContent = `@${effectiveHandle} · публикация, теги и очередь`;
  if (headTitle) headTitle.textContent = `Публикация: ${effectiveName || `Профиль #${profile.id}`}`;
  const stats = storageProfileStats();
  const youtube = storageProfileLinkedYoutube();
  const youtubeLabel = youtube
    ? `<span class="badge b-err"><i class="ti ti-brand-youtube"></i>${esc(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}</span>`
    : '<span class="badge">YouTube не привязан</span>';
  el.innerHTML = `<div class="storage-channel storage-channel-compact">
    <div class="storage-channel-banner compact" style="${storageProfileBannerStyle(profile)}"></div>
    <div class="storage-channel-head compact">
      ${storageProfileAvatarHtml(profile, 'storage-channel-avatar compact')}
      <div class="storage-channel-titlebox">
        <div class="storage-channel-name">${esc(effectiveName)}</div>
        <div class="storage-channel-meta mono dim">@${esc(effectiveHandle)} · ${stats.total} видео · ${stats.ready} готово · ${stats.queued} в очереди ${youtubeLabel}</div>
        <p>${esc(effectiveDescription)}</p>
      </div>
      <div class="row-actions storage-profile-head-actions">
        <button class="btn-secondary" onclick="openStorageProfileVideoPicker()">Добавить видео</button>
        <button class="btn-mini" onclick="openStorageProfileDrawer('processing')">Обработка</button>
        <button class="btn-mini" onclick="openStorageProfileDrawer('tags')">Теги</button>
        <button class="btn-mini" onclick="openStorageProfileDrawer('publish')">Настройки</button>
      </div>
    </div>
    ${storageProfileTabs()}
    ${storageProfilePublishControls()}
    <div class="storage-profile-content">${storageProfileMainContent(profile)}</div>
    ${renderStorageProfileDrawer(profile)}
  </div>`;
}
async function saveStorageProfile() {
  if (!currentStorageProfileId) return;
  const profile = currentStorageProfile || {};
  try {
    const data = await api.patch(`/api/storage-profiles/${Number(currentStorageProfileId)}`, {
      name: document.getElementById('storage-profile-name')?.value ?? profile.name ?? '',
      handle: document.getElementById('storage-profile-handle')?.value ?? profile.handle ?? '',
      description: document.getElementById('storage-profile-description')?.value ?? profile.description ?? '',
      avatar_initials: document.getElementById('storage-profile-initials')?.value ?? profile.avatar_initials ?? '',
      avatar_color: document.getElementById('storage-profile-avatar-color')?.value ?? profile.avatar_color ?? '',
      avatar_url: document.getElementById('storage-profile-avatar-url')?.value ?? profile.avatar_url ?? '',
      banner_color: document.getElementById('storage-profile-banner-color')?.value ?? profile.banner_color ?? '',
      banner_url: document.getElementById('storage-profile-banner-url')?.value ?? profile.banner_url ?? '',
    });
    currentStorageProfile = data.profile;
    storageProfiles = storageProfiles.map(profile => Number(profile.id) === Number(data.profile.id) ? data.profile : profile);
    renderStorageProfilesGrid();
    renderStorageProfileDetail();
    showToast('Профиль сохранён');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось сохранить профиль');
  }
}
async function disableStorageProfile() {
  if (!currentStorageProfileId) return;
  if (!confirm('Отключить локальный профиль? Видео на диске не удаляются.')) return;
  try {
    await api.del(`/api/storage-profiles/${Number(currentStorageProfileId)}`);
    currentStorageProfileId = null;
    currentStorageProfile = null;
    storageProfileItems = [];
    showToast('Профиль отключён');
    await openStorageProfilesHub({replace: true});
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось отключить профиль');
  }
}
async function searchStorageCatalogVideos(query = storageCatalogSearchQuery) {
  storageCatalogSearchQuery = String(query || '');
  const data = await api.get(`/api/catalog/videos/search?q=${encodeURIComponent(storageCatalogSearchQuery)}&limit=60`);
  storageProfileCandidates = data.items || [];
  selectedStorageCandidatePaths = new Set(Array.from(selectedStorageCandidatePaths).filter(path => storageProfileCandidates.some(item => item.workspace_path === path)));
}
function onStorageCatalogSearchInput(value) {
  storageCatalogSearchQuery = value || '';
  if (storageCatalogSearchTimer) clearTimeout(storageCatalogSearchTimer);
  storageCatalogSearchTimer = setTimeout(async () => {
    try {
      await searchStorageCatalogVideos(storageCatalogSearchQuery);
      renderStorageProfileDetail();
      setTimeout(() => {
        const input = document.getElementById('storage-video-search-input');
        if (input) {
          input.focus();
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }, 0);
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось выполнить поиск видео');
    }
  }, 220);
}
async function loadRandomStorageCatalogVideos() {
  try {
    storageCatalogSearchQuery = '';
    const data = await api.get('/api/catalog/videos/random?limit=24');
    storageProfileCandidates = data.items || [];
    selectedStorageCandidatePaths.clear();
    renderStorageProfileDetail();
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось загрузить случайные видео');
  }
}
async function toggleStorageCandidatePicker() {
  if (!currentStorageProfileId) {
    showToast('Сначала выберите профиль', 'err');
    return;
  }
  if (storageProfileDrawerOpen && storageProfileDrawerSection === 'add-video') {
    closeStorageProfileDrawer();
    return;
  }
  await openStorageProfileVideoPicker();
}
async function openStorageProfileVideoPicker() {
  if (!currentStorageProfileId) {
    showToast('Сначала выберите профиль', 'err');
    return;
  }
  storageCandidatePickerOpen = true;
  storageProfileDrawerOpen = true;
  storageProfileDrawerSection = 'add-video';
  try {
    await searchStorageCatalogVideos('');
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось загрузить каталог видео');
  }
  renderStorageProfileDetail();
}
function toggleStorageCandidateSelection(workspacePath, checked) {
  if (checked) selectedStorageCandidatePaths.add(workspacePath);
  else selectedStorageCandidatePaths.delete(workspacePath);
  renderStorageProfileDetail();
}
async function addCandidateToStorageProfile(workspacePath) {
  if (!currentStorageProfileId) return;
  try {
    const candidate = storageProfileCandidates.find(item => item.workspace_path === workspacePath);
    const data = await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/items`, {
      workspace_path: workspacePath,
      status: candidate?.is_publish_ready ? 'ready' : 'draft',
    });
    currentStorageProfile = data.profile || currentStorageProfile;
    showToast('Видео добавлено в профиль');
    storageCandidatePickerOpen = false;
    storageProfileDrawerOpen = false;
    await loadStorageProfileDetail(currentStorageProfileId);
    await loadStorageProfiles({selectId: currentStorageProfileId});
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось добавить видео');
  }
}
async function addSelectedCatalogVideosToStorageProfile() {
  const paths = Array.from(selectedStorageCandidatePaths);
  if (!paths.length) return;
  let added = 0;
  let errors = 0;
  for (const path of paths) {
    try {
      const candidate = storageProfileCandidates.find(item => item.workspace_path === path);
      await api.post(`/api/storage-profiles/${Number(currentStorageProfileId)}/items`, {
        workspace_path: path,
        status: candidate?.is_publish_ready ? 'ready' : 'draft',
      });
      added += 1;
    } catch {
      errors += 1;
    }
  }
  selectedStorageCandidatePaths.clear();
  storageCandidatePickerOpen = false;
  storageProfileDrawerOpen = false;
  await loadStorageProfileDetail(currentStorageProfileId);
  await loadStorageProfiles({selectId: currentStorageProfileId});
  showToast(`Добавлено видео: ${added}${errors ? ` · ошибок: ${errors}` : ''}`, errors ? 'err' : 'ok');
}
async function removeStorageProfileItem(itemId) {
  if (!currentStorageProfileId) return;
  try {
    const data = await api.del(`/api/storage-profiles/${Number(currentStorageProfileId)}/items/${Number(itemId)}`);
    currentStorageProfile = data.profile || currentStorageProfile;
    storageProfileItems = storageProfileItems.filter(item => Number(item.id) !== Number(itemId));
    selectedStorageProfileItemIds.delete(Number(itemId));
    showToast('Видео убрано из профиля');
    renderStorageProfileDetail();
    await loadStorageProfiles({selectId: currentStorageProfileId});
  } catch (err) {
    showStorageProfileError(err.message || 'Не удалось убрать видео');
  }
}
async function ensureStorageProfilesLoaded() {
  if (storageProfiles.length) return storageProfiles;
  const data = await api.get('/api/storage-profiles');
  storageProfiles = data.items || [];
  return storageProfiles;
}
async function pickStorageProfileIdForAdd() {
  const profiles = await ensureStorageProfilesLoaded();
  if (!profiles.length) {
    nav('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
    showToast('Сначала создайте локальный профиль', 'err');
    return null;
  }
  if (currentStorageProfileId && profiles.some(profile => Number(profile.id) === Number(currentStorageProfileId))) {
    return Number(currentStorageProfileId);
  }
  if (profiles.length === 1) return Number(profiles[0].id);
  return await openStorageProfilePickModal(profiles);
}
async function addWorkspacePathToStorageProfile(path) {
  try {
    const workspacePath = await workspacePathForStorageProfile(path);
    const profileId = await pickStorageProfileIdForAdd();
    if (!profileId) return;
    await api.post(`/api/storage-profiles/${Number(profileId)}/items`, {workspace_path: workspacePath});
    showToast('Видео добавлено в локальный профиль');
    if (currentView === 'storage-profile' && Number(currentStorageProfileId) === Number(profileId)) {
      await loadStorageProfileDetail(profileId);
    } else if (currentView === 'storage-profiles') {
      currentStorageProfileId = Number(profileId);
      await loadStorageProfiles({selectId: profileId});
    }
  } catch (err) {
    showToast(err.message || 'Не удалось добавить видео в профиль', 'err');
  }
}
async function addWorkspaceItemToStorageProfile(key) {
  const item = workspaceItemByKey(key);
  if (!item) return;
  await addWorkspacePathToStorageProfile(item.path || item.prepared_path || item.source_path || '');
}
function storageProfileWorkspaceButton(item) {
  if (!item || item.missing || !item.file_exists) {
    return `<button class="btn-mini" disabled title="${esc(item?.path_error || 'Файл отсутствует')}">В профиль</button>`;
  }
  return `<button class="btn-mini" onclick="event.stopPropagation();addWorkspaceItemToStorageProfile('${esc(item.id)}')">В профиль</button>`;
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

function getActiveEditingProfiles() {
  return (editingProfiles || []).filter(profile => profile.enabled);
}

function syncWorkspaceEditingSelection() {
  const profiles = getActiveEditingProfiles();
  if (!profiles.some(profile => Number(profile.id) === Number(workspaceEditingState.selectedProfileId))) {
    workspaceEditingState.selectedProfileId = profiles[0] ? Number(profiles[0].id) : null;
  }
}

function onWorkspaceEditingProfileChange(value) {
  workspaceEditingState.selectedProfileId = value ? Number(value) : null;
  renderWorkspaceEditingControls();
}

function renderWorkspaceEditingControls() {
  const profileSelect = document.getElementById('workspace-editing-profile');
  const templateSelect = document.getElementById('workspace-editing-template');
  const reactionSelect = document.getElementById('workspace-editing-reaction');
  const planBtn = document.getElementById('workspace-editing-plan-btn');
  const stateEl = document.getElementById('workspace-editing-state');
  if (!profileSelect || !templateSelect || !reactionSelect || !planBtn || !stateEl) return;

  const profiles = getActiveEditingProfiles();
  syncWorkspaceEditingSelection();
  if (!profiles.length) {
    profileSelect.innerHTML = '<option value="">Нет профилей каналов</option>';
    profileSelect.disabled = true;
  } else {
    profileSelect.disabled = workspaceEditingState.busy;
    profileSelect.innerHTML = profiles.map(profile =>
      `<option value="${profile.id}"${Number(profile.id) === Number(workspaceEditingState.selectedProfileId) ? ' selected' : ''}>${esc(profile.name)}</option>`
    ).join('');
  }

  const currentTemplate = templateSelect.value;
  templateSelect.innerHTML = `<option value="">Из профиля канала</option>${activeStudioEditingTemplates().map(item => `<option value="${item.studio_template_id || item.id}">${esc(item.name)} · v${Number(item.version || 1)}</option>`).join('')}`;
  if (currentTemplate && Array.from(templateSelect.options).some(option => option.value === currentTemplate)) templateSelect.value = currentTemplate;
  renderWorkspaceEditingTemplateParams();

  const currentReaction = reactionSelect.value;
  reactionSelect.innerHTML = `<option value="">Из пула реакций / без реакции</option>${(editingReactions || []).filter(item => item.enabled).map(item => `<option value="${item.id}">${esc(item.name)}${item.file_exists ? '' : ' · файл отсутствует'}</option>`).join('')}`;
  if (currentReaction && Array.from(reactionSelect.options).some(option => option.value === currentReaction)) reactionSelect.value = currentReaction;

  const selectedCount = selectedWorkspaceKeys.size;
  planBtn.disabled = workspaceEditingState.busy || !workspaceEditingState.selectedProfileId || selectedCount === 0;
  stateEl.textContent = profiles.length
    ? `Выбрано элементов workspace: ${selectedCount}. Задача создаётся без запуска рендера.`
    : 'Создайте профиль канала в «Профили» → «Обработка».';
}

function resolveWorkspaceEditingTemplate() {
  const explicitId = editingOptionalId(document.getElementById('workspace-editing-template')?.value);
  if (explicitId) {
    return activeStudioEditingTemplates().find(item => Number(item.studio_template_id || item.id) === Number(explicitId)) || null;
  }
  const profile = editingProfiles.find(item => Number(item.id) === Number(workspaceEditingState.selectedProfileId));
  const defaultId = profile?.default_studio_template_id;
  if (!defaultId) return null;
  return activeStudioEditingTemplates().find(item => Number(item.studio_template_id || item.id) === Number(defaultId)) || null;
}

function renderWorkspaceEditingTemplateParams() {
  const el = document.getElementById('workspace-editing-template-params');
  if (!el) return;
  const template = resolveWorkspaceEditingTemplate();
  const params = template?.parameters || template?.definition?.parameters || {};
  const entries = Object.entries(params);
  if (!template || !entries.length) {
    el.innerHTML = '';
    return;
  }
  const label = key => ({
    reaction_position: 'Позиция реакции',
    reaction_height: 'Высота реакции',
    pip_position: 'PIP позиция',
    main_fit: 'Основное видео',
    reaction_fit: 'Reaction fit',
    background_color: 'Фон',
    main_volume: 'Громкость видео',
    reaction_volume: 'Громкость реакции',
    mute_reaction: 'Заглушить реакцию',
    top_text: 'Верхний текст',
    bottom_text: 'Нижний текст',
  })[key] || key;
  const control = ([key, meta]) => {
    const type = meta?.type || 'text';
    const value = meta?.default ?? '';
    const attrs = `data-workspace-template-param="${esc(key)}"`;
    if (type === 'select') {
      const options = (meta.values || []).map(item => `<option value="${esc(item)}"${String(item) === String(value) ? ' selected' : ''}>${esc(item)}</option>`).join('');
      return `<label class="mini-field"><span class="field-lbl">${esc(label(key))}</span><select ${attrs}>${options}</select></label>`;
    }
    if (type === 'boolean') {
      return `<label class="toggle-label workspace-youtube-toggle"><input type="checkbox" ${attrs} ${value ? 'checked' : ''}> ${esc(label(key))}</label>`;
    }
    if (type === 'color') {
      return `<label class="mini-field"><span class="field-lbl">${esc(label(key))}</span><input type="color" ${attrs} value="${esc(value || '#000000')}"></label>`;
    }
    if (type === 'number') {
      const min = meta.min !== undefined ? ` min="${esc(meta.min)}"` : '';
      const max = meta.max !== undefined ? ` max="${esc(meta.max)}"` : '';
      return `<label class="mini-field"><span class="field-lbl">${esc(label(key))}</span><input type="number" step="any"${min}${max} ${attrs} value="${esc(value)}"></label>`;
    }
    return `<label class="mini-field"><span class="field-lbl">${esc(label(key))}</span><input type="text" maxlength="${Number(meta.max_length || 200)}" ${attrs} value="${esc(value)}"></label>`;
  };
  el.innerHTML = `<details class="workspace-editing-param-details"><summary>Параметры Studio template · ${esc(template.name)}</summary><div class="workspace-editing-param-grid">${entries.map(control).join('')}</div></details>`;
}

function collectWorkspaceEditingParameterValues() {
  const values = {};
  document.querySelectorAll('[data-workspace-template-param]').forEach(field => {
    const key = field.dataset.workspaceTemplateParam;
    if (!key) return;
    if (field.type === 'checkbox') values[key] = Boolean(field.checked);
    else if (field.type === 'number') values[key] = field.value === '' ? null : Number(field.value);
    else values[key] = field.value;
  });
  return values;
}

function workspaceEditingSummary(data) {
  const summary = data?.summary || {};
  return `Создано: ${summary.created || 0} · существующих: ${summary.existing || 0} · пропущено: ${summary.skipped || 0} · ошибок: ${summary.errors || 0}`;
}

async function planSelectedWorkspaceEditing() {
  const itemKeys = Array.from(selectedWorkspaceKeys);
  if (!itemKeys.length) {
    showToast('Сначала выберите элементы workspace', 'err');
    return;
  }
  if (!workspaceEditingState.selectedProfileId) {
    showToast('Сначала выберите профиль канала', 'err');
    return;
  }
  workspaceEditingState.busy = true;
  renderWorkspaceEditingControls();
  try {
    const data = await api.post('/api/editing/jobs/plan', {
      item_keys: itemKeys,
      channel_profile_id: Number(workspaceEditingState.selectedProfileId),
      studio_template_id: editingOptionalId(document.getElementById('workspace-editing-template')?.value),
      reaction_asset_id: editingOptionalId(document.getElementById('workspace-editing-reaction')?.value),
      parameter_values: collectWorkspaceEditingParameterValues(),
      force_new: Boolean(document.getElementById('workspace-editing-force-new')?.checked),
    });
    showToast(workspaceEditingSummary(data));
    const skipped = (data.results || []).filter(item => item.status === 'skipped' || item.status === 'error');
    if (skipped.length) {
      alert(skipped.slice(0, 20).map(item => `${item.item_key}: ${item.reason}`).join('\n'));
    }
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || 'Не удалось добавить в очередь рендера', 'err');
  } finally {
    workspaceEditingState.busy = false;
    renderWorkspaceEditingControls();
  }
}

function renderWorkspaceType(item) {
  const cls = item.item_type === 'clip' ? 'workspace-type clip' : 'workspace-type segment';
  return `<span class="${cls}">${esc(workspaceTypeLabel(item))}</span>`;
}
function missingBadge(item) {
  return item?.missing ? '<span class="badge b-err">Файл отсутствует</span>' : '';
}
function sourceDeletedBadge(item) {
  return item?.source_deleted ? '<span class="badge b-warn">Исходник удалён</span>' : '';
}
function prepareBadge(item) {
  if (!item) return '';
  if (item.prepare_status === 'done' && item.prepared_file_exists) return '<span class="badge b-ok">Подготовлено</span>';
  if ((item.target_aspect || 'original') !== 'original') return '<span class="badge b-warn">Нужно подготовить</span>';
  if (item.prepare_status === 'failed') return '<span class="badge b-err">Ошибка подготовки</span>';
  return '';
}
function workspaceOpenFileButton(item, label='Смотреть') {
  const path = item?.path || item?.source_path || '';
  if (!path || item?.missing || !item?.file_exists) {
    return `<button class="btn-mini" disabled title="${esc(item?.path_error || 'Файл отсутствует')}">${esc(label)}</button>`;
  }
  return webPlayerButton(path, label);
}
function workspaceOpenFolderButton(item, label='Папка') {
  if (!item?.folder_path || !item?.folder_exists) {
    return `<button class="btn-mini" disabled title="${esc(item?.path_error || 'Папка отсутствует')}">${esc(label)}</button>`;
  }
  return `<button class="btn-mini" data-path="${esc(item.folder_path)}" onclick="event.stopPropagation();goToOutputFolder(this.dataset.path)">${esc(label)}</button>`;
}
function toggleWorkspaceSelection(key, checked) {
  if (checked) selectedWorkspaceKeys.add(key);
  else selectedWorkspaceKeys.delete(key);
  renderWorkspaceBulkState();
  renderWorkspaceEditingControls();
}
function selectWorkspaceItem(key) {
  workspaceDetailDirty = false;
  currentWorkspaceItemKey = key;
  if (key) selectedWorkspaceKeys.add(key);
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  closeInspector();
}
function renderWorkspaceBulkState() {
  const total = selectedWorkspaceKeys.size;
  document.querySelectorAll('[data-workspace-selected-count]').forEach(el => {
    el.textContent = total ? `Выбрано: ${total}` : '';
  });
  renderWorkspaceEditingControls();
  renderWorkspaceActionBar();
}
function renderWorkspaceActionBar() {
  const total = selectedWorkspaceKeys.size;
  if (currentView !== 'queue' || !total) {
    if (currentView === 'queue') renderActionBar();
    return;
  }
  renderActionBar(`Клипов выбрано: ${total}`, `
    <button class="btn-mini" onclick="clearWorkspaceSelection()">Снять</button>
    <button class="btn-mini" onclick="bulkSetWorkspaceStatus('ready')">Готово</button>
    <button class="btn-mini" onclick="bulkAddCatalogTagToWorkspaceItems()">Добавить тег</button>
    <button class="btn-danger" onclick="bulkDeleteWorkspaceItems()">Удалить</button>
  `);
}
function renderWorkspaceListAndDetail() {
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  renderWorkspaceFilterControls();
}
function workspaceFilterTagChip(tag, mode) {
  if (!tag) return '';
  const remove = `<button class="tag-remove" title="Убрать фильтр" onclick="removeWorkspaceFilterTag(${Number(tag.id)}, '${esc(mode)}')">×</button>`;
  const prefix = mode === 'exclude' ? 'исключить: ' : '';
  return `<span class="tag-filter-chip ${mode === 'exclude' ? 'exclude' : 'include'}">${tagPill({...tag, name: `${prefix}${tag.name}`})}${remove}</span>`;
}
function renderWorkspaceFilterControls() {
  const search = document.getElementById('workspace-search-input');
  if (search && search.value !== workspaceSearchQuery) search.value = workspaceSearchQuery;
  const select = document.getElementById('workspace-filter-tag-select');
  if (select) {
    const previous = select.value;
    select.innerHTML = tagOptionsHtml([], {assignableOnly: true, emptyLabel: 'Выберите тег'});
    if (previous && Array.from(select.options).some(option => option.value === previous)) select.value = previous;
  }
  const chips = document.getElementById('workspace-filter-active-tags');
  if (chips) {
    const include = Array.from(workspaceFilterIncludeTagIds)
      .map(id => workspaceFilterTagChip(catalogTagById(id), 'include'))
      .join('');
    const exclude = Array.from(workspaceFilterExcludeTagIds)
      .map(id => workspaceFilterTagChip(catalogTagById(id), 'exclude'))
      .join('');
    const query = workspaceSearchQuery ? `<span class="filter-query-chip">поиск: ${esc(workspaceSearchQuery)} <button class="tag-remove" onclick="onWorkspaceSearchInput('')">×</button></span>` : '';
    chips.innerHTML = include || exclude || query
      ? `<div class="tag-pill-list">${query}${include}${exclude}</div>`
      : 'Фильтры тегов не выбраны';
  }
  const visibleLine = document.querySelector('[data-workspace-filter-summary]');
  if (visibleLine) {
    const parentItems = workspaceItemsForParentFilter();
    visibleLine.textContent = `Показано: ${getVisibleWorkspaceItems().length} из ${parentItems.length}`;
  }
}
function onWorkspaceSearchInput(value) {
  workspaceSearchQuery = String(value || '');
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  renderWorkspaceFilterControls();
}
function addWorkspaceFilterTag(mode = 'include') {
  const tagId = Number(document.getElementById('workspace-filter-tag-select')?.value || 0);
  if (!tagId) {
    showToast('Выберите тег для фильтра', 'err');
    return;
  }
  if (mode === 'exclude') {
    workspaceFilterIncludeTagIds.delete(tagId);
    workspaceFilterExcludeTagIds.add(tagId);
  } else {
    workspaceFilterExcludeTagIds.delete(tagId);
    workspaceFilterIncludeTagIds.add(tagId);
  }
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  renderWorkspaceFilterControls();
}
function removeWorkspaceFilterTag(tagId, mode = 'include') {
  if (mode === 'exclude') workspaceFilterExcludeTagIds.delete(Number(tagId));
  else workspaceFilterIncludeTagIds.delete(Number(tagId));
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  renderWorkspaceFilterControls();
}
function clearWorkspaceTagFilters() {
  workspaceSearchQuery = '';
  workspaceFilterIncludeTagIds.clear();
  workspaceFilterExcludeTagIds.clear();
  renderClipsTable(getVisibleWorkspaceItems());
  renderWorkspaceDetail();
  renderWorkspaceFilterControls();
}
function renderWorkspaceTagControls() {
  const select = document.getElementById('workspace-bulk-catalog-tag');
  if (!select) return;
  const previous = select.value;
  select.innerHTML = tagOptionsHtml([], {assignableOnly: true, emptyLabel: 'Выберите тег'});
  if (previous && Array.from(select.options).some(option => option.value === previous)) {
    select.value = previous;
  }
}
async function setCatalogTagForWorkspaceItems(itemKeys, tagId, action = 'add') {
  const selected = (itemKeys || [])
    .map(key => workspaceItemByKey(key))
    .filter(item => item && workspaceCatalogPath(item) && !item.missing);
  if (!selected.length) {
    showToast('Выберите видео внутри workspace', 'err');
    return;
  }
  if (!tagId) {
    showToast('Выберите тег', 'err');
    return;
  }
  try {
    let updated = 0;
    for (const item of selected) {
      const currentIds = catalogTagIds(workspaceCatalogTags(item), {includeStatus: true});
      const nextIds = action === 'remove'
        ? currentIds.filter(id => id !== Number(tagId))
        : Array.from(new Set(currentIds.concat([Number(tagId)])));
      await updateVideoCatalogTags(workspaceCatalogPath(item), nextIds);
      updated += 1;
    }
    renderWorkspaceListAndDetail();
    renderGlobalTagsManager();
    showToast(action === 'remove' ? `Тег снят с видео: ${updated}` : `Тег добавлен к видео: ${updated}`);
  } catch (err) {
    showToast(err.message || 'Не удалось обновить теги видео', 'err');
  }
}
async function bulkAddCatalogTagToWorkspaceItems() {
  const tagId = Number(document.getElementById('workspace-bulk-catalog-tag')?.value || 0);
  await setCatalogTagForWorkspaceItems(Array.from(selectedWorkspaceKeys), tagId, 'add');
}
async function bulkRemoveCatalogTagFromWorkspaceItems() {
  const tagId = Number(document.getElementById('workspace-bulk-catalog-tag')?.value || 0);
  await setCatalogTagForWorkspaceItems(Array.from(selectedWorkspaceKeys), tagId, 'remove');
}
function renderClipsTable(rows) {
  const el = document.getElementById('clips-table');
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = '<div class="empty">Нарезанных сегментов и клипов пока нет. После нарезки видео файлы появятся здесь.</div>';
    return;
  }
  if (uiState.clipViewMode === 'grid') {
    el.innerHTML = `<div class="workspace-selected-line mono dim"><span data-workspace-selected-count></span><span data-workspace-filter-summary></span></div><div class="media-grid workspace-media-grid">${rows.map(item => {
      const selected = selectedWorkspaceKeys.has(item.id);
      const playablePath = item.path || item.source_path;
      const title = workspaceTitle(item);
      const tags = workspaceCatalogTags(item);
      return `<article class="media-card ${selected ? 'selected' : ''} ${item.missing ? 'missing' : ''}" onclick="selectWorkspaceItem('${esc(item.id)}')">
        <div class="media-card-thumb">
          <label class="media-card-check" onclick="event.stopPropagation()"><input type="checkbox" ${selected ? 'checked' : ''} onchange="toggleWorkspaceSelection('${esc(item.id)}', this.checked);renderClipsTable(getVisibleWorkspaceItems())"></label>
          ${item.missing ? videoThumb(playablePath, title) : videoWatchThumb(playablePath, title)}
        </div>
        <div class="media-card-body">
          <div class="media-card-title" title="${esc(title)}">${esc(title)}</div>
          <div class="media-card-meta">${badge(item.workspace_status)}${sourceDeletedBadge(item)} · ${esc(formatDurationSec(item.duration_sec))} · ${esc(workspaceTypeLabel(item))}</div>
          <div class="media-card-path" title="${esc(item.path || '')}">${esc(workspaceDisplayPath(item.workspace_path || item.path || ''))}</div>
          ${tags.length ? `<div class="media-card-meta">${tagListPills(tags.slice(0, 4))}</div>` : ''}
          <div class="media-card-actions">${workspaceOpenFileButton(item)}${workspaceOpenFolderButton(item)}${storageProfileWorkspaceButton(item)}</div>
        </div>
      </article>`;
    }).join('')}</div>`;
    renderWorkspaceBulkState();
    renderWorkspaceFilterControls();
    return;
  }
  el.innerHTML = `<div class="workspace-selected-line mono dim"><span data-workspace-selected-count></span><span data-workspace-filter-summary></span></div><div class="table-scroll"><table class="tbl workspace-table"><thead><tr><th></th><th>Файл</th><th>Источник</th><th>Длит.</th><th>Тип</th><th>Статус</th><th>Путь</th><th>Действие</th></tr></thead><tbody>${rows.map(item => {
    const selected = selectedWorkspaceKeys.has(item.id);
    const activeClasses = ['workspace-row'];
    if (currentWorkspaceItemKey === item.id) activeClasses.push('active');
    if (item.missing) activeClasses.push('missing');
    const active = ` class="${activeClasses.join(' ')}"`;
    const playablePath = item.path || item.source_path;
    const title = workspaceTitle(item);
    const renderInfo = item.render_status ? `<div class="mono dim">render: ${esc(ruStatus(item.render_status))}</div>` : '';
    const publishInfo = item.publish_job_status ? `<div class="mono dim">publish #${esc(item.publish_job_id || '')}: ${esc(ruStatus(item.publish_job_status))}</div>` : '';
    const prepareInfo = `<div class="mono dim">format: ${esc(targetAspectLabel(item.target_aspect))}${item.prepare_status && item.prepare_status !== 'none' ? ` · ${esc(ruStatus(item.prepare_status))}` : ''}</div>`;
    const tagInfo = workspaceCatalogTags(item).length ? tagListPills(workspaceCatalogTags(item)) : '';
    const thumb = item.missing ? videoThumb(playablePath, title) : videoWatchThumb(playablePath, title);
    const titleCell = item.missing
      ? `<div class="mono txt ov" title="${esc(title)}">${esc(title)}</div>`
      : `<button class="link-video mono txt ov" data-path="${esc(playablePath)}" title="${esc(title)}" onclick="event.stopPropagation();openWebPlayer(this.dataset.path,{title:this.textContent||''})">${esc(title)}</button>`;
    return `<tr${active} data-key="${esc(item.id)}" onclick="selectWorkspaceItem('${esc(item.id)}')"><td><input type="checkbox" ${selected ? 'checked' : ''} onclick="event.stopPropagation();toggleWorkspaceSelection('${esc(item.id)}', this.checked)"></td><td><div class="video-name-cell">${thumb}<div style="min-width:0;flex:1">${titleCell}<div class="mono dim">#${esc(item.item_id)} · ${esc(item.file_name || '—')}</div>${renderInfo}${prepareInfo}${publishInfo}${tagInfo}</div></div></td><td class="mono mid ov">${esc(item.video_title || '—')}</td><td class="mono txt">${esc(formatDurationSec(item.duration_sec))}</td><td>${renderWorkspaceType(item)}</td><td><div class="status-stack">${badge(item.workspace_status)}${sourceDeletedBadge(item)}${missingBadge(item)}${prepareBadge(item)}</div></td><td><span class="mono dim ov" title="${esc(item.path || '')}">${esc(shortPath(item.path || '—'))}</span></td><td><div class="row-actions">${workspaceOpenFileButton(item)}${workspaceOpenFolderButton(item)}${storageProfileWorkspaceButton(item)}${item.missing ? `<button class="btn-mini" onclick="event.stopPropagation();deleteWorkspaceItem('${esc(item.id)}')">Убрать</button>` : ''}</div></td></tr>`;
  }).join('')}</tbody></table></div>`;
  renderWorkspaceBulkState();
  renderWorkspaceFilterControls();
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
  renderWorkspaceEditingControls();
}
async function refreshWorkspaceList() {
  const data = await api.get('/api/workspace/clips');
  lastClips = data.items || [];
  if (currentWorkspaceItemKey && !workspaceItemByKey(currentWorkspaceItemKey)) {
    currentWorkspaceItemKey = null;
  }
  renderClipCounts(data.counts || workspaceCountsFromItems(lastClips));
  renderWorkspaceTagControls();
  return lastClips;
}
function workspaceYoutubeRequestBody(items) {
  return {
    item_keys: items,
    account_id: Number(workspaceYoutubeState.selectedAccountId),
    publish_mode: document.getElementById('workspace-youtube-mode')?.value || 'public',
    category_id: document.getElementById('workspace-youtube-category')?.value || '22',
    made_for_kids: Boolean(document.getElementById('workspace-youtube-made-for-kids')?.checked),
  };
}
function workspaceYoutubeSummary(data) {
  const prepared = data?.prepared || 0;
  const created = data?.created || 0;
  const updated = data?.updated || 0;
  const skipped = data?.skipped || 0;
  const errors = data?.errors || 0;
  return `Подготовлено: ${prepared} · добавлено в очередь: ${created} · обновлено: ${updated} · пропущено: ${skipped} · ошибок: ${errors}`;
}
function workspaceYoutubeSkippedText(data) {
  const skipped = data?.skipped_items || [];
  if (!skipped.length) return '';
  return skipped.slice(0, 12).map(item => `${item.item_key}: ${item.reason}`).join('\n');
}
function confirmYoutubeBatch(count, mode, actionText) {
  const visibility = mode || 'public';
  if (count > 5) {
    const ok = confirm(`Вы собираетесь ${actionText || 'отправить'} ${count} видео в YouTube. Видимость: ${visibility}. Продолжить?`);
    if (!ok) return false;
  }
  if (visibility === 'public') {
    return confirm('Видео будут опубликованы публично. Это действие может быть видно зрителям. Продолжить?');
  }
  return true;
}
function publishVisibilitySummary(jobs, fallback = 'public') {
  const values = Array.from(new Set((jobs || []).map(job => job.privacy_status || job.publish_mode || fallback).filter(Boolean)));
  if (!values.length) return fallback;
  return values.length === 1 ? values[0] : values.join('/');
}
function confirmPublishJobsBatch(jobs, count, actionText) {
  const selectedJobs = jobs || [];
  const effectiveCount = Number(count || selectedJobs.length || 0);
  const visibility = publishVisibilitySummary(selectedJobs, 'public');
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
  const mode = document.getElementById('workspace-youtube-mode')?.value || 'public';
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
    const runnableJobs = (data.created || 0) + (data.updated || 0);
    if (runNow && runnableJobs > 0) {
      const worker = await api.post('/api/publish/worker/run-once', {limit: Math.max(1, runnableJobs)});
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
async function refreshWorkspaceFromPrepareResponse(data) {
  const workspace = data?.workspace || {};
  lastClips = workspace.items || lastClips;
  renderClipCounts(workspace.counts || workspaceCountsFromItems(lastClips));
  renderWorkspaceListAndDetail();
}
async function prepareSelectedWorkspaceItems() {
  const items = Array.from(selectedWorkspaceKeys);
  if (!items.length) {
    showToast('Сначала выберите сегменты или клипы', 'err');
    return;
  }
  const target = document.getElementById('workspace-bulk-target-aspect')?.value || 'original';
  try {
    const data = await api.post('/api/workspace/clips/bulk-prepare', {item_keys: items, target_aspect: target});
    await refreshWorkspaceFromPrepareResponse(data);
    showToast(`Подготовлено: ${data.prepared || 0} · пропущено: ${data.skipped || 0} · ошибок: ${data.errors || 0}`);
    const skipped = data.skipped_items || [];
    if (skipped.length) alert(`Пропущенные элементы:\n${skipped.slice(0, 12).map(item => `${item.item_key}: ${item.reason}`).join('\n')}`);
  } catch (err) {
    showToast(err.message || 'Не удалось подготовить выбранные', 'err');
  }
}
async function prepareCurrentWorkspaceItem() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item) return;
  try {
    await saveWorkspaceDetail({silent: true, rerender: false});
    const target = document.getElementById('workspace-target-aspect')?.value || item.target_aspect || 'original';
    const data = await api.post(`/api/workspace/clips/${encodeURIComponent(item.id)}/prepare`, {target_aspect: target});
    const updated = data.item;
    lastClips = lastClips.map(row => row.id === updated.id ? updated : row);
    currentWorkspaceItemKey = updated.id;
    renderWorkspaceListAndDetail();
    showToast(`Видео подготовлено: ${targetAspectLabel(updated.target_aspect)}`);
  } catch (err) {
    showToast(err.message || 'Не удалось подготовить видео', 'err');
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
  const removeFromProfiles = confirm('Также удалить это видео из локальных профилей?');
  try {
    const suffix = removeFromProfiles ? '?remove_from_profiles=true' : '';
    const data = await api.del(`/api/workspace/clips/${encodeURIComponent(item.id)}${suffix}`);
    await refreshWorkspaceFromDeleteResponse(data);
    const removed = data.result?.profile_items?.removed || 0;
    showToast(`${item.file_exists ? 'Файл удалён' : 'Запись убрана из списка'}${removeFromProfiles ? ` · из профилей: ${removed}` : ''}`);
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
  const removeFromProfiles = confirm('Также удалить выбранные видео из локальных профилей?');
  try {
    const data = await api.post('/api/workspace/clips/bulk-delete', {
      items: selected,
      remove_from_profiles: removeFromProfiles,
    });
    await refreshWorkspaceFromDeleteResponse(data);
    const profilePart = removeFromProfiles ? ` · из профилей: ${data.summary?.profile_items_removed || 0}` : '';
    showToast(`${workspaceDeleteSummary(data.summary)}${profilePart}`);
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
function workspaceCatalogTagsPanel(item) {
  const tags = workspaceCatalogTags(item);
  const assignableIds = catalogAssignableTags().map(tag => Number(tag.id));
  const selectedIds = catalogTagIds(tags, {includeStatus: false}).filter(id => assignableIds.includes(id));
  const currentOptions = tagOptionsHtml(selectedIds, {assignableOnly: true, onlySelected: true});
  const addOptions = tagOptionsHtml(selectedIds, {assignableOnly: true, emptyLabel: 'Выберите тег'});
  const disabled = !workspaceCatalogPath(item) || item.missing;
  return `<div class="workspace-catalog-tags-panel">
    <div class="storage-tag-panel-head">
      <div>
        <div class="field-lbl">Каталоговые теги</div>
        <div class="mono dim">Эти теги используются профилями, поиском и будущей автоматикой. Статусные теги появляются автоматически.</div>
      </div>
      <button class="btn-mini" onclick="openGlobalTagsView()">Открыть Теги</button>
    </div>
    ${tagListPills(tags)}
    <div class="workspace-tag-editor">
      <select id="workspace-catalog-tag-add" ${disabled ? 'disabled' : ''}>${addOptions}</select>
      <button class="btn-secondary" ${disabled ? 'disabled' : ''} onclick="addCatalogTagToCurrentWorkspaceItem()">Добавить</button>
      <select id="workspace-catalog-tag-remove" ${disabled || !selectedIds.length ? 'disabled' : ''}>${currentOptions}</select>
      <button class="btn-mini" ${disabled || !selectedIds.length ? 'disabled' : ''} onclick="removeCatalogTagFromCurrentWorkspaceItem()">Снять</button>
    </div>
  </div>`;
}
async function addCatalogTagToCurrentWorkspaceItem() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  const tagId = Number(document.getElementById('workspace-catalog-tag-add')?.value || 0);
  if (!item || !tagId) return;
  await setCatalogTagForWorkspaceItems([item.id], tagId, 'add');
}
async function removeCatalogTagFromCurrentWorkspaceItem() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  const tagId = Number(document.getElementById('workspace-catalog-tag-remove')?.value || 0);
  if (!item || !tagId) return;
  await setCatalogTagForWorkspaceItems([item.id], tagId, 'remove');
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
  const publishPanel = item.publish_job_id
    ? `<div class="missing-panel publish-panel">${badge(item.publish_job_status || 'queued')}<div><b>Задача публикации #${esc(item.publish_job_id)}</b><p>${item.publish_youtube_url ? `<a class="link-video mono txt" href="${esc(item.publish_youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>` : 'YouTube URL пока нет.'}${item.publish_error ? `<br><span class="err">${esc(shortErrorText(item.publish_error))}</span>` : ''}</p></div></div>`
    : '';
  const preparedPanel = `<div class="missing-panel publish-panel">${prepareBadge(item) || badge(item.prepare_status || 'none')}<div><b>Подготовленный файл</b><p>${item.prepared_path ? `<span title="${esc(item.prepared_path)}">${esc(shortPath(item.prepared_path))}</span>` : 'Файл ещё не подготовлен.'}${item.prepare_error ? `<br><span class="err">${esc(shortErrorText(item.prepare_error))}</span>` : ''}</p></div></div>`;
  el.innerHTML = `<div class="workspace-detail-body">
    <div class="workspace-preview">${item.missing ? videoThumb(playablePath, title) : videoWatchThumb(playablePath, title)}</div>
    <div class="workspace-detail-head">
      <div>
        <div class="workspace-detail-title">${esc(title)}</div>
        <div class="mono dim detail-badges">${renderWorkspaceType(item)} · #${esc(item.item_id)} · ${badge(item.workspace_status)} ${sourceDeletedBadge(item)} ${missingBadge(item)}</div>
      </div>
    </div>
    ${missingNotice}
    ${publishPanel}
    ${preparedPanel}
    <div class="workspace-meta">
      <div><span>Источник</span><b>${esc(item.video_title || '—')}</b></div>
      <div><span>Длительность</span><b>${esc(formatDurationSec(item.duration_sec))}</b></div>
      <div><span>Файл</span><b title="${esc(item.path || '')}">${esc(shortPath(item.path || '—'))}</b></div>
      <div><span>Папка</span><b title="${esc(item.folder_path || '')}">${esc(shortPath(item.folder_path || '—'))}</b></div>
    </div>
    <div class="field"><label class="field-lbl">Статус</label><select id="workspace-status" onchange="markWorkspaceDetailDirty();updateWorkspaceDetailActionState()"><option value="draft">Черновик</option><option value="ready">Готово</option><option value="queued">В очереди</option><option value="uploaded">Загружено</option><option value="failed">Ошибка</option></select></div>
    ${workspaceCatalogTagsPanel(item)}
    <div class="field"><label class="field-lbl">Формат видео</label><select id="workspace-target-aspect" onchange="markWorkspaceDetailDirty()"><option value="original">Original</option><option value="16x9">16:9</option><option value="9x16">9:16</option></select></div>
    <div class="field"><label class="field-lbl">Название</label><input id="workspace-title" type="text" value="${esc(item.title || '')}" placeholder="${esc(item.file_name || title)}" oninput="markWorkspaceDetailDirty()"></div>
    <div class="field"><label class="field-lbl">Описание</label><textarea id="workspace-description" rows="5" placeholder="Локальное описание для будущей публикации" oninput="markWorkspaceDetailDirty()">${esc(item.description || '')}</textarea></div>
    <div class="field"><label class="field-lbl">Теги публикации (текст)</label><input id="workspace-tags" type="text" value="${esc(item.tags || '')}" placeholder="через запятую" oninput="markWorkspaceDetailDirty()"></div>
    <div class="workspace-detail-actions">
      <button class="btn-primary" onclick="saveWorkspaceDetail()">Сохранить</button>
      ${workspaceOpenFileButton(item, 'Смотреть')}
      ${workspaceOpenFolderButton(item, 'Открыть папку')}
      ${storageProfileWorkspaceButton(item)}
      <button class="btn-secondary" onclick="prepareCurrentWorkspaceItem()">Подготовить видео</button>
      <button class="btn-secondary" onclick="setCurrentWorkspaceStatus('ready')"${readyDisabled}>Сделать готовым</button>
      <button class="btn-secondary" onclick="setCurrentWorkspaceStatus('draft')"${draftDisabled}>Вернуть в черновики</button>
      ${fileAction}
      <button class="btn-secondary stub-action" onclick="futureFeature('Субтитры')">Добавить субтитры</button>
      <button class="btn-secondary stub-action" onclick="futureFeature('Уникализация')">Уникализировать</button>
    </div>
  </div>`;
  const statusEl = document.getElementById('workspace-status');
  if (statusEl) statusEl.value = item.workspace_status || 'draft';
  const aspectEl = document.getElementById('workspace-target-aspect');
  if (aspectEl) aspectEl.value = item.target_aspect || 'original';
  updateWorkspaceDetailActionState();
}
function markWorkspaceDetailDirty() {
  workspaceDetailDirty = true;
}
function currentWorkspaceFormPayload(item) {
  return {
    workspace_status: document.getElementById('workspace-status')?.value || item.workspace_status,
    title: document.getElementById('workspace-title')?.value || '',
    description: document.getElementById('workspace-description')?.value || '',
    tags: document.getElementById('workspace-tags')?.value || '',
    target_aspect: document.getElementById('workspace-target-aspect')?.value || item.target_aspect || 'original',
  };
}
function updateWorkspaceDetailActionState() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  const status = document.getElementById('workspace-status')?.value || item?.workspace_status || 'draft';
  const canRefreshPublishJob = ['queued', 'failed', 'cancelled'].includes(item?.publish_job_status);
  const canPublish = Boolean(getWorkspaceYoutubeAccount())
    && Boolean(item)
    && !item.missing
    && (status === 'ready' || canRefreshPublishJob);
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
    workspaceDetailDirty = false;
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
async function updateCurrentWorkspaceYoutubeMetadata() {
  const item = workspaceItemByKey(currentWorkspaceItemKey);
  if (!item?.publish_job_id || item.publish_job_status !== 'done') return;
  try {
    const updated = await saveWorkspaceDetail({silent: true, rerender: false});
    if (!updated) return;
    await api.post(
      `/api/publish/jobs/${encodeURIComponent(item.publish_job_id)}/youtube/update-metadata`,
      {
        title: updated.title || '',
        description: updated.description || '',
        tags: updated.tags || '',
      },
    );
    await refreshWorkspaceList();
    renderWorkspaceListAndDetail();
    await refreshPublishJobs();
    showToast('Данные видео на YouTube обновлены.');
  } catch (err) {
    await refreshWorkspaceList().catch(() => {});
    renderWorkspaceListAndDetail();
    showToast(err.message || 'Не удалось обновить данные на YouTube', 'err');
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
  ['publish-connect-btn', 'publish-connect-btn-accounts', 'integrations-connect-btn'].forEach(id => {
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
    el.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('error')}<span class="mono txt">OAuth-клиент не настроен</span></div><p>Создайте OAuth-клиент в Google Cloud, затем импортируйте JSON в разделе «Интеграции». После этого можно подключить YouTube-канал.</p><div class="action-row"><button class="btn-secondary" onclick="openYouTubeSettings()">Открыть Интеграции</button></div></div>`;
    return;
  }

  if (!lastYoutubeAccounts.length) {
    const source = selectedProfile ? ` · ${oauthProfileSourceLabel(selectedProfile)}` : '';
    const hint = publishState.onboardingHint ? `<p class="err">${esc(publishState.onboardingHint)}</p>` : '';
    el.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('active')}<span class="mono txt">OAuth-клиент готов${esc(source)}</span></div><p>Выберите OAuth-клиент и нажмите «Подключить канал», чтобы открыть Google Consent Screen.</p>${hint}</div>`;
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
      : 'Сначала добавьте OAuth-клиент в настройках YouTube OAuth.';
    stateEl.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge(profiles.length ? 'active' : 'error')}<span class="mono txt">YouTube-каналы ещё не подключены</span></div><p>${esc(text)}</p></div>`;
    listEl.innerHTML = '<div class="empty">Подключённых YouTube-каналов пока нет.</div>';
    return;
  }

  stateEl.innerHTML = `<div class="setup-panel"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge('active')}<span class="mono txt">Подключённые каналы</span></div><p>Здесь можно проверить подключённые каналы и отключить лишние.</p></div>`;
  listEl.innerHTML = `<table class="tbl"><thead><tr><th>#</th><th>Аккаунт</th><th>Канал</th><th>Google OAuth-клиент</th><th>Статус</th><th>Подключён</th><th>Обновлён</th><th>Действие</th></tr></thead><tbody>${lastYoutubeAccounts.map(account => {
    const displayName = account.display_name || account.channel_title || 'YouTube аккаунт';
    const channel = account.channel_title || account.channel_id || '—';
    const profile = account.profile_name || (account.oauth_profile_id ? `Профиль #${account.oauth_profile_id}` : 'по умолчанию');
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
    select.innerHTML = '<option value="">OAuth-клиент не найден</option>';
    select.disabled = true;
    if (meta) meta.innerHTML = '<div>Создайте OAuth-клиент в Google Cloud и импортируйте JSON в настройках.</div>';
    return;
  }
  select.disabled = false;
  select.innerHTML = profiles.map(profile => {
    const suffix = [
      profile.is_default ? 'по умолчанию' : '',
      oauthProfileSourceLabel(profile),
    ].filter(Boolean).join(' · ');
    return `<option value="${Number(profile.id)}"${Number(profile.id) === Number(publishState.selectedProfileId) ? ' selected' : ''}>${esc(profile.name || `Профиль #${profile.id}`)}${suffix ? ` · ${esc(suffix)}` : ''}</option>`;
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
    if (meta) meta.innerHTML = '<div>Подключите канал через выбранный OAuth-клиент.</div>';
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
    const title = clip.video_title || `Клип #${clip.id}`;
    return `<tr><td class="mono dim">#${clip.id}</td><td><div class="video-name-cell">${videoWatchThumb(playable, title)}<div style="min-width:0;flex:1"><button class="link-video mono txt ov" data-path="${esc(playable)}" onclick="openWebPlayer(this.dataset.path,{title:this.textContent||''})">${esc(title)}</button><div class="mono dim ov">${esc(shortPath(clip.output_path || playable || '—'))}</div></div></div></td><td class="mono mid">${esc(clip.video_title || '—')}</td><td class="mono dim ov">${esc(shortPath(clip.output_path || '—'))}</td><td><div class="row-actions"><button class="btn-mini${selected ? ' on' : ''}" onclick="selectPublishClip(${Number(clip.id)})">Выбрать</button>${mpvButton(playable)}</div></td></tr>`;
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
  el.innerHTML = `<div class="selection-card-body"><div class="selection-title">Выбран клип</div><div class="selected-video-row">${videoWatchThumb(playable, clip.video_title || 'clip')}<div style="min-width:0;flex:1"><button class="link-video selection-name" data-path="${esc(playable)}" onclick="openWebPlayer(this.dataset.path,{title:this.textContent||''})">${esc(clip.video_title || `Клип #${clip.id}`)}</button><div class="selection-meta mono">${esc(shortPath(clip.output_path || playable || '—'))}</div></div><div class="row-actions">${mpvButton(playable)}</div></div></div>`;
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
  const scheduleCounts = {all: 0, untimed: 0, scheduled: 0, overdue: 0};
  lastPublishJobs.forEach(job => {
    if (hiddenDonePublishJobIds.has(Number(job.id))) return;
    scheduleCounts.all += 1;
    if (job.schedule_state === 'untimed') scheduleCounts.untimed += 1;
    else scheduleCounts.scheduled += 1;
    if (job.schedule_state === 'overdue') scheduleCounts.overdue += 1;
  });
  Object.entries(scheduleCounts).forEach(([key, value]) => {
    const el = document.getElementById('schedule-cnt-' + key);
    if (el) el.textContent = value || '';
  });
}
function getVisiblePublishJobs() {
  return lastPublishJobs.filter(job => {
    if (hiddenDonePublishJobIds.has(Number(job.id))) return false;
    if (publishScheduleFilter === 'untimed' && job.schedule_state !== 'untimed') return false;
    if (publishScheduleFilter === 'scheduled' && job.schedule_state === 'untimed') return false;
    if (publishScheduleFilter === 'overdue' && job.schedule_state !== 'overdue') return false;
    if (publishJobFilter === 'all') return true;
    return job.status === publishJobFilter;
  });
}
function filterPublishSchedule(tab, status) {
  publishScheduleFilter = status || 'untimed';
  document.querySelectorAll('[data-schedule-filter]').forEach(item => item.classList.remove('on'));
  if (tab) tab.classList.add('on');
  renderPublishJobsTable();
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
function formatScheduleCountdown(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const value = Number(seconds);
  const minutes = Math.floor(Math.abs(value) / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const text = days ? `${days}д ${hours % 24}ч` : hours ? `${hours}ч ${minutes % 60}м` : `${minutes}м`;
  return value >= 0 ? `через ${text}` : `${text} назад`;
}
function formatMoscowDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}
function renderPublishScheduleCell(job) {
  if (job.schedule_state === 'untimed') return '<span class="mono dim">Без таймера</span>';
  const group = job.schedule_group_name
    ? `<button class="link-video mono" onclick="openPublishScheduleEditor(${Number(job.schedule_group_id)})">${esc(job.schedule_group_name)}</button>`
    : '';
  const publish = job.publish_at ? `<div class="mono dim">публикация: ${esc(formatMoscowDate(job.publish_at))}</div>` : '';
  return `<div>${group}<div><span class="schedule-state ${esc(job.schedule_state)}">${esc(ruStatus(job.schedule_state))}</span></div><div class="mono txt">загрузка: ${esc(formatMoscowDate(job.upload_at))}</div>${publish}<div class="mono dim">${esc(formatScheduleCountdown(job.seconds_until_upload))}</div></div>`;
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
  el.innerHTML = `<div class="workspace-selected-line mono dim" data-publish-selected-count></div><table class="tbl publish-jobs-table"><thead><tr><th></th><th>Задача</th><th>Статус</th><th>Название</th><th>Канал</th><th>Расписание</th><th>Видимость</th><th>Файл</th><th>Создано</th><th>Попытки</th><th>Ошибка</th><th>YouTube</th><th>Действие</th></tr></thead><tbody>${rows.map(job => {
    const selected = selectedPublishJobIds.has(Number(job.id));
    const youtubeLink = job.youtube_url ? `<a class="btn-mini" href="${esc(job.youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>` : '—';
    const clipPath = job.clip_output_path || job.video_source_path || '';
    const profile = job.profile_name ? `<div class="mono dim">${esc(job.profile_name)}</div>` : '';
    const err = job.error ? `<button class="link-video err mono" title="${esc(job.error)}" onclick="showPublishJobError(${Number(job.id)})">${esc(shortErrorText(job.error))}</button><button class="btn-mini" onclick="copyPublishJobError(${Number(job.id)})">Копировать</button>` : '—';
    const actions = [];
    if (job.can_retry) actions.push(`<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Повторить</button>`);
    if (job.can_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)})">Запустить сейчас</button>`);
    else if (job.can_force_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)}, true)">Запустить принудительно</button>`);
    if (job.schedule_state === 'overdue' && job.schedule_group_id) actions.push(`<button class="btn-secondary" onclick="approvePublishScheduleGroup(${Number(job.schedule_group_id)})">Разрешить</button>`);
    if (job.can_cancel) actions.push(`<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Отменить</button>`);
    actions.push(webPlayerButton(clipPath, 'Смотреть'));
    return `<tr><td><input type="checkbox" ${selected ? 'checked' : ''} onclick="togglePublishJobSelection(${Number(job.id)}, this.checked)"></td><td class="mono dim">#${job.id}</td><td>${badge(job.status)}</td><td class="mono mid ov" title="${esc(job.title || '')}">${esc(job.title || '—')}</td><td><div class="mono txt">${esc(job.channel_title || job.account_display_name || '—')}</div>${profile}</td><td>${renderPublishScheduleCell(job)}</td><td class="mono dim">${esc(job.privacy_status || 'public')}<div>${esc(job.publish_mode || 'public')}</div></td><td class="mono dim ov" title="${esc(clipPath)}">${esc(shortPath(clipPath || '—'))}</td><td class="mono dim">${esc(formatMtime(job.created_at))}</td><td class="mono txt">${esc(job.attempt_count || 0)}</td><td><div class="row-actions">${err}</div></td><td>${youtubeLink}</td><td><div class="row-actions">${actions.join('')}</div></td></tr>`;
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
    const [profilesData, accountsData, clipsData, jobsData, groupsData] = await Promise.all([
      api.get('/api/publish/youtube/oauth-profiles'),
      api.get('/api/publish/youtube/accounts'),
      api.get('/api/clips?status=done&limit=200'),
      api.get('/api/publish/jobs?limit=200'),
      api.get('/api/publish/schedule-groups'),
    ]);
    lastYoutubeProfiles = profilesData.profiles || [];
    lastYoutubeAccounts = accountsData.accounts || [];
    lastReadyPublishClips = clipsData.clips || [];
    lastPublishJobs = jobsData.jobs || [];
    lastPublishScheduleGroups = groupsData.groups || [];
    syncPublishSelections();
    renderPublishView();
  } catch (err) {
    if (!silent) renderPublishError(`Не удалось загрузить публикацию:\n${err.message || err}`);
  }
}

async function refreshPublishJobs() {
  try {
    const [data, groupsData] = await Promise.all([
      api.get('/api/publish/jobs?limit=200'),
      api.get('/api/publish/schedule-groups'),
    ]);
    lastPublishJobs = data.jobs || [];
    lastPublishScheduleGroups = groupsData.groups || [];
    renderPublishJobsTable();
    if (currentView === 'queue') {
      await refreshWorkspaceList();
      renderWorkspaceListAndDetail();
    }
    if ((currentView === 'storage-profiles' || currentView === 'storage-profile') && currentStorageProfileId) {
      await refreshStorageProfilePublishState();
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
function moscowInputValue(value) {
  if (!value) return '';
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(date).reduce((result, part) => {
    result[part.type] = part.value;
    return result;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}
function moscowInputToIso(value) {
  return value ? `${value}:00+03:00` : null;
}
function defaultMoscowInput(minutes = 60) {
  return moscowInputValue(new Date(Date.now() + minutes * 60000).toISOString());
}
function scheduleEditorJobs() {
  return editingPublishScheduleJobIds
    .map(id => lastPublishJobs.find(job => Number(job.id) === Number(id)))
    .filter(Boolean);
}
function closePublishScheduleEditor(event = null) {
  if (event && event.target?.id !== 'publish-schedule-modal') return;
  const modal = document.getElementById('publish-schedule-modal');
  if (modal) modal.style.display = 'none';
}
function openPublishScheduleEditor(groupId = null) {
  const group = groupId
    ? lastPublishScheduleGroups.find(item => Number(item.id) === Number(groupId))
    : null;
  const jobs = group
    ? (group.jobs || []).filter(job => job.status === 'queued')
    : selectedPublishJobList().filter(job => job.status === 'queued');
  if (!jobs.length) {
    showToast('Выберите задачи в очереди для расписания', 'err');
    return;
  }
  editingPublishScheduleGroupId = group ? Number(group.id) : null;
  editingPublishScheduleJobIds = jobs.map(job => Number(job.id));
  editingPublishScheduleInitial = group;
  document.getElementById('schedule-modal-title').textContent = group ? `Расписание #${group.id}` : 'Новое расписание';
  document.getElementById('schedule-group-name').value = group?.name || `Группа ${new Date().toLocaleDateString('ru-RU')}`;
  document.getElementById('schedule-upload-mode').value = group?.upload?.mode || 'same';
  document.getElementById('schedule-publish-mode').value = group?.publish?.mode || 'none';
  document.getElementById('schedule-remove-btn').style.display = group ? '' : 'none';
  document.getElementById('schedule-approve-btn').style.display = group?.jobs?.some(job => job.schedule_state === 'overdue') ? '' : 'none';
  hideInlineError('schedule-form-error');
  renderScheduleEditorFields();
  document.getElementById('publish-schedule-modal').style.display = 'grid';
}
function scheduleSpecFields(kind) {
  const mode = document.getElementById(`schedule-${kind}-mode`)?.value || 'none';
  const spec = editingPublishScheduleInitial?.[kind] || {};
  const el = document.getElementById(`schedule-${kind}-fields`);
  if (!el) return;
  if (mode === 'none') {
    el.innerHTML = '<div class="mono dim">Таймер не применяется.</div>';
    return;
  }
  if (mode === 'same' || mode === 'interval') {
    const start = moscowInputValue(spec.start_at) || defaultMoscowInput(kind === 'publish' ? 120 : 60);
    const interval = Number(spec.interval_minutes || 30);
    el.innerHTML = `<div class="field"><label class="field-lbl">Дата и время · Europe/Moscow</label><input id="schedule-${kind}-start" type="datetime-local" value="${esc(start)}" oninput="renderSchedulePreview()"></div>${mode === 'interval' ? `<div class="field"><label class="field-lbl">Интервал, минут</label><input id="schedule-${kind}-interval" type="number" min="1" value="${interval}" oninput="renderSchedulePreview()"></div>` : ''}`;
    return;
  }
  const itemTimes = spec.item_times || {};
  el.innerHTML = `<div class="schedule-individual-list">${scheduleEditorJobs().map(job => {
    const value = moscowInputValue(itemTimes[String(job.id)] || itemTimes[job.id]) || defaultMoscowInput(kind === 'publish' ? 120 : 60);
    return `<label class="schedule-individual-row"><span class="mono ov">#${job.id} ${esc(job.title || '')}</span><input data-schedule-kind="${kind}" data-job-id="${job.id}" type="datetime-local" value="${esc(value)}" oninput="renderSchedulePreview()"></label>`;
  }).join('')}</div>`;
}
function renderScheduleEditorFields() {
  scheduleSpecFields('upload');
  scheduleSpecFields('publish');
  renderSchedulePreview();
}
function scheduleSpecBody(kind) {
  const mode = document.getElementById(`schedule-${kind}-mode`)?.value || 'none';
  const result = {mode, start_at: null, interval_minutes: null, item_times: {}};
  if (mode === 'same' || mode === 'interval') {
    result.start_at = moscowInputToIso(document.getElementById(`schedule-${kind}-start`)?.value || '');
    if (mode === 'interval') result.interval_minutes = Number(document.getElementById(`schedule-${kind}-interval`)?.value || 0);
  }
  if (mode === 'individual') {
    document.querySelectorAll(`[data-schedule-kind="${kind}"]`).forEach(input => {
      result.item_times[Number(input.dataset.jobId)] = moscowInputToIso(input.value);
    });
  }
  return result;
}
function expandSchedulePreview(spec, jobs) {
  if (spec.mode === 'none') return jobs.map(() => null);
  if (spec.mode === 'individual') return jobs.map(job => spec.item_times[Number(job.id)] || null);
  const start = spec.start_at ? new Date(spec.start_at) : null;
  if (!start || Number.isNaN(start.getTime())) return jobs.map(() => null);
  const interval = spec.mode === 'interval' ? Number(spec.interval_minutes || 0) : 0;
  return jobs.map((job, index) => new Date(start.getTime() + index * interval * 60000).toISOString());
}
function renderSchedulePreview() {
  const el = document.getElementById('schedule-preview');
  if (!el) return;
  const jobs = scheduleEditorJobs();
  const uploads = expandSchedulePreview(scheduleSpecBody('upload'), jobs);
  const publishes = expandSchedulePreview(scheduleSpecBody('publish'), jobs);
  el.innerHTML = `<div class="schedule-preview-table"><table class="tbl compact"><thead><tr><th>Задача</th><th>Видео</th><th>Начало загрузки</th><th>Публикация</th></tr></thead><tbody>${jobs.map((job, index) => `<tr><td class="mono">#${job.id}</td><td class="mono ov">${esc(job.title || '—')}</td><td class="mono">${esc(formatMoscowDate(uploads[index]))}</td><td class="mono">${esc(formatMoscowDate(publishes[index]))}</td></tr>`).join('')}</tbody></table></div>`;
}
async function savePublishScheduleGroup() {
  const body = {
    name: document.getElementById('schedule-group-name')?.value || '',
    job_ids: editingPublishScheduleJobIds,
    upload: scheduleSpecBody('upload'),
    publish: scheduleSpecBody('publish'),
  };
  hideInlineError('schedule-form-error');
  try {
    if (editingPublishScheduleGroupId) {
      await api.patch(`/api/publish/schedule-groups/${editingPublishScheduleGroupId}`, body);
    } else {
      await api.post('/api/publish/schedule-groups', body);
    }
    closePublishScheduleEditor();
    selectedPublishJobIds.clear();
    await refreshPublishJobs();
    showToast('Расписание сохранено');
  } catch (err) {
    showInlineError('schedule-form-error', err.message || 'Не удалось сохранить расписание');
  }
}
async function removeEditingScheduleGroup() {
  if (!editingPublishScheduleGroupId || !confirm('Снять расписание со всех задач группы?')) return;
  try {
    await api.del(`/api/publish/schedule-groups/${editingPublishScheduleGroupId}`);
    closePublishScheduleEditor();
    await refreshPublishJobs();
    showToast('Расписание снято');
  } catch (err) {
    showInlineError('schedule-form-error', err.message || 'Не удалось снять расписание');
  }
}
async function approvePublishScheduleGroup(groupId) {
  if (!confirm('Разрешить запуск всех просроченных задач в очереди этой группы?')) return;
  try {
    const data = await api.post(`/api/publish/schedule-groups/${groupId}/approve-overdue`, {});
    await refreshPublishJobs();
    showToast(`Разрешено просроченных задач: ${data.approved || 0}`);
  } catch (err) {
    renderPublishError(err.message || 'Не удалось разрешить просроченные задачи');
  }
}
async function approveEditingScheduleGroup() {
  if (!editingPublishScheduleGroupId) return;
  await approvePublishScheduleGroup(editingPublishScheduleGroupId);
  closePublishScheduleEditor();
}
function nextRunnablePublishJobs(limit) {
  return lastPublishJobs
    .filter(job => job.status === 'queued' && job.schedule_state === 'untimed')
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
    showToast(`Обработано задач публикации: ${data.processed || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось обработать очередь публикации:\n${err.message || err}`);
  }
}

async function runSelectedPublishJobs() {
  const jobs = selectedPublishJobList().filter(job => job.can_force_run);
  if (!jobs.length) {
    showToast('Среди выбранных нет задач для запуска', 'err');
    return;
  }
  const force = jobs.some(job => job.schedule_state === 'waiting' || job.schedule_state === 'overdue');
  if (force && !confirm('Среди выбранных есть будущие или просроченные задачи. Принудительно запустить их сейчас?')) return;
  if (!confirmPublishJobsBatch(jobs, jobs.length, 'запустить загрузку для')) return;
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/jobs/bulk-run', {
      job_ids: jobs.map(job => Number(job.id)),
      force,
    });
    showToast(`Запущено: ${data.summary?.processed || 0} · ошибок: ${data.summary?.errors || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось запустить выбранные задачи:\n${err.message || err}`);
  }
}

function renderIntegrationsError(message) {
  if (message) showInlineError('integrations-error', message);
  else hideInlineError('integrations-error');
}
function integrationLinkedProfiles(account) {
  return account?.linked_storage_profiles || [];
}
function integrationAccountSearchText(account) {
  return [
    account.id,
    account.display_name,
    account.local_alias,
    account.account_email,
    account.channel_id,
    account.channel_title,
    account.official_channel_title,
    account.channel_handle,
    account.channel_custom_url,
    account.channel_description,
    account.uploads_playlist_id,
    account.profile_name,
    ...(integrationLinkedProfiles(account).map(profile => `${profile.name} ${profile.handle}`)),
  ].join(' ').toLowerCase();
}
function formatChannelNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return new Intl.NumberFormat('ru-RU', {notation: 'compact', maximumFractionDigits: 1}).format(num);
}
function youtubeAccountAvatarHtml(account) {
  const url = account.channel_avatar_url || '';
  const title = account.channel_title || account.display_name || 'YT';
  const initials = title.trim().slice(0, 2).toUpperCase() || 'YT';
  return url
    ? `<img class="youtube-account-avatar" src="${esc(url)}" alt="">`
    : `<div class="youtube-account-avatar fallback">${esc(initials)}</div>`;
}
function youtubeAccountStatsHtml(account) {
  const subscribers = account.hidden_subscriber_count ? 'скрыто' : formatChannelNumber(account.subscriber_count);
  return `<div class="youtube-account-stats">
    <span title="Подписчики">👥 ${esc(subscribers)}</span>
    <span title="Просмотры">▶ ${esc(formatChannelNumber(account.view_count))}</span>
    <span title="Видео">▦ ${esc(formatChannelNumber(account.video_count))}</span>
  </div>`;
}
function renderIntegrationsOAuthSelect() {
  const select = document.getElementById('integrations-profile-select');
  const meta = document.getElementById('integrations-profile-meta');
  if (!select) return;
  const profiles = getActiveOAuthProfiles();
  if (!profiles.length) {
    select.innerHTML = '<option value="">OAuth profile не найден</option>';
    select.disabled = true;
    if (meta) meta.innerHTML = '<div>Создайте или импортируйте Google API auth слева.</div>';
    return;
  }
  if (!profiles.some(profile => Number(profile.id) === Number(publishState.selectedProfileId))) {
    const defaultProfile = profiles.find(profile => profile.is_default) || profiles[0];
    publishState.selectedProfileId = Number(defaultProfile.id);
  }
  select.disabled = false;
  select.innerHTML = profiles.map(profile => {
    const suffix = [profile.is_default ? 'по умолчанию' : '', oauthProfileSourceLabel(profile)].filter(Boolean).join(' · ');
    return `<option value="${Number(profile.id)}"${Number(profile.id) === Number(publishState.selectedProfileId) ? ' selected' : ''}>${esc(profile.name || `OAuth #${profile.id}`)}${suffix ? ` · ${esc(suffix)}` : ''}</option>`;
  }).join('');
  const selected = getSelectedProfile();
  if (meta && selected) {
    meta.innerHTML = `<div>${esc(oauthProfileSourceLabel(selected))} · ${selected.client_secret_set ? 'secret сохранён' : 'secret не задан'}</div><div>Redirect URI: <span class="mono">${esc(selected.redirect_uri || '—')}</span></div>`;
  }
}
function renderIntegrationsConnectState() {
  const el = document.getElementById('integrations-connect-state');
  if (!el) return;
  const profiles = getActiveOAuthProfiles();
  if (!profiles.length) {
    el.innerHTML = `<div class="setup-panel">${badge('error')} <span class="mono txt">Нет активного Google API auth</span><p>Импортируйте OAuth Client JSON или создайте auth вручную.</p></div>`;
    return;
  }
  const accountCount = (lastYoutubeAccounts || []).filter(account => (account.status || 'active') === 'active').length;
  el.innerHTML = `<div class="setup-panel">${badge('active')} <span class="mono txt">Готово к подключению YouTube</span><p>Подключённых каналов: ${accountCount}. Выберите Google API auth и нажмите «Подключить канал».</p></div>`;
}
function renderIntegrationsOAuthProfilesPanel() {
  const el = document.getElementById('integrations-oauth-profiles');
  if (!el) return;
  const rows = lastYoutubeProfiles || [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty">Google API auth пока нет. Импортируйте JSON OAuth-клиента или создайте профиль вручную.</div>';
    return;
  }
  el.innerHTML = `<table class="tbl compact"><thead><tr><th>#</th><th>Название</th><th>Client</th><th>Redirect</th><th>Статус</th><th>Действие</th></tr></thead><tbody>${rows.map(profile => {
    const mode = `${oauthProfileSourceLabel(profile)}${profile.is_default ? ' · по умолчанию' : ''}`;
    const actions = [
      `<button class="btn-mini" onclick="editIntegrationOAuthProfile(${Number(profile.id)})">Редактировать</button>`,
      profile.is_default ? '' : `<button class="btn-mini" onclick="setIntegrationDefaultOAuthProfile(${Number(profile.id)})">По умолчанию</button>`,
      isEnvOAuthProfile(profile) ? '' : `<button class="btn-danger" onclick="deleteIntegrationOAuthProfile(${Number(profile.id)})">Удалить</button>`,
    ].filter(Boolean).join('');
    return `<tr><td class="mono dim">#${profile.id}</td><td><div class="mono txt">${esc(profile.name || `OAuth #${profile.id}`)}</div><div class="mono dim">${esc(mode)}</div></td><td class="mono dim ov">${esc(profile.client_id || '—')}</td><td class="mono dim ov">${esc(profile.redirect_uri || '—')}</td><td>${badge(profile.status || 'active')}</td><td><div class="row-actions">${actions}</div></td></tr>`;
  }).join('')}</tbody></table>`;
}
function renderIntegrationsAccountsPanel() {
  const el = document.getElementById('integrations-accounts-list');
  if (!el) return;
  const input = document.getElementById('integrations-search');
  integrationsSearchQuery = (input?.value || integrationsSearchQuery || '').trim().toLowerCase();
  const accounts = (lastYoutubeAccounts || []).filter(account => !integrationsSearchQuery || integrationAccountSearchText(account).includes(integrationsSearchQuery));
  if (!accounts.length) {
    el.innerHTML = '<div class="empty">YouTube-аккаунты не найдены. Подключите канал или измените поиск.</div>';
    return;
  }
  el.innerHTML = `<table class="tbl integrations-accounts-table"><thead><tr><th>#</th><th>YouTube канал</th><th>Google API auth</th><th>Профили</th><th>Sync</th><th>Действие</th></tr></thead><tbody>${accounts.map(account => {
    const officialName = account.channel_title || account.official_channel_title || `YouTube #${account.id}`;
    const alias = account.display_name || account.local_alias || '';
    const handle = account.channel_handle || account.channel_custom_url || '';
    const oauth = account.oauth_profile?.name || account.profile_name || (account.oauth_profile_id ? `OAuth #${account.oauth_profile_id}` : 'не указан');
    const linked = integrationLinkedProfiles(account);
    const linkedHtml = linked.length
      ? linked.map(profile => `<button class="link-video mono" onclick="openStorageProfile(${Number(profile.id)})">${esc(profile.name || `Профиль #${profile.id}`)}</button>`).join('<span class="mono dim">, </span>')
      : '<span class="mono dim">не привязан</span>';
    const disabled = account.status === 'disconnected';
    const accountError = account.error ? `<div class="mono err">${esc(shortErrorText(account.error))}</div>` : '';
    const syncError = account.metadata_sync_error ? `<div class="mono err" title="${esc(account.metadata_sync_error)}">${esc(shortErrorText(account.metadata_sync_error))}</div>` : '';
    const description = account.channel_description ? `<div class="mono dim ov" title="${esc(account.channel_description)}">${esc(account.channel_description)}</div>` : '';
    const published = account.channel_published_at ? `<span>создан: ${esc(formatMtime(account.channel_published_at))}</span>` : '';
    const country = account.channel_country ? `<span>страна: ${esc(account.channel_country)}</span>` : '';
    const syncMeta = [
      account.metadata_synced_at ? `sync: ${formatMtime(account.metadata_synced_at)}` : 'sync: ещё не было',
      account.uploads_playlist_id ? `uploads: ${shortPath(account.uploads_playlist_id)}` : '',
    ].filter(Boolean).join(' · ');
    return `<tr>
      <td class="mono dim">#${account.id}</td>
      <td>
        <div class="youtube-account-cell">
          ${youtubeAccountAvatarHtml(account)}
          <div class="youtube-account-main">
            <div class="mono txt">${esc(officialName)}</div>
            ${alias && alias !== officialName ? `<div class="mono dim">alias: ${esc(alias)}</div>` : ''}
            <div class="mono dim">${esc([account.account_email, account.channel_id].filter(Boolean).join(' · '))}</div>
            ${handle ? `<div class="mono dim">${esc(handle)}</div>` : ''}
            ${description}
            <div class="youtube-account-meta">${[published, country].filter(Boolean).join(' · ')}</div>
            ${youtubeAccountStatsHtml(account)}
            ${accountError}
          </div>
        </div>
      </td>
      <td class="mono dim">${esc(oauth)}</td>
      <td>${linkedHtml}</td>
      <td><div>${badge(account.status || 'active')}</div><div class="mono dim">${esc(syncMeta)}</div>${syncError}</td>
      <td><div class="row-actions">
        <button class="btn-mini" onclick="syncYouTubeAccountMetadata(${Number(account.id)})">Обновить данные</button>
        <button class="btn-mini" onclick="editYouTubeAccountAlias(${Number(account.id)})">Alias</button>
        <button class="btn-danger" ${disabled ? 'disabled' : ''} onclick="disconnectYouTubeAccount(${Number(account.id)})">Отключить</button>
      </div></td>
    </tr>`;
  }).join('')}</tbody></table>`;
}
function renderIntegrationsView() {
  renderPublishConnectButton();
  renderIntegrationsOAuthSelect();
  renderIntegrationsConnectState();
  renderIntegrationsOAuthProfilesPanel();
  renderIntegrationsAccountsPanel();
}
async function loadIntegrationsView(options = {}) {
  const {silent = false} = options;
  renderIntegrationsError('');
  try {
    const [profilesData, accountsData, storageProfilesData] = await Promise.all([
      api.get('/api/publish/youtube/oauth-profiles'),
      api.get('/api/publish/youtube/accounts'),
      api.get('/api/storage-profiles').catch(() => ({items: storageProfiles || []})),
    ]);
    lastYoutubeProfiles = profilesData.profiles || [];
    lastYoutubeAccounts = accountsData.accounts || [];
    storageProfiles = storageProfilesData.items || storageProfiles || [];
    syncPublishSelections();
    renderIntegrationsView();
  } catch (err) {
    if (!silent) renderIntegrationsError(`Не удалось загрузить интеграции:\n${err.message || err}`);
  }
}
function onIntegrationOAuthProfileChange(value) {
  publishState.selectedProfileId = value ? Number(value) : null;
  renderIntegrationsView();
}

const INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI = 'http://127.0.0.1:8000/api/publish/youtube/oauth/callback';
let integrationOAuthFormMode = 'json';
let integrationOAuthEditingProfileId = null;

function setIntegrationOAuthFormError(message) {
  if (message) {
    showInlineError('integration-oauth-form-error', message);
  } else {
    hideInlineError('integration-oauth-form-error');
  }
}

function setIntegrationOAuthFormMode(mode = 'json') {
  integrationOAuthFormMode = mode === 'edit' ? 'edit' : (mode === 'manual' ? 'manual' : 'json');
  const title = document.getElementById('integration-oauth-modal-title');
  const jsonWrap = document.getElementById('integration-oauth-json-wrap');
  const manualWrap = document.getElementById('integration-oauth-manual-wrap');
  const saveBtn = document.getElementById('integration-oauth-save-btn');
  const hint = document.querySelector('.integration-oauth-next-hint');
  if (title) {
    title.textContent = integrationOAuthFormMode === 'edit'
      ? 'Редактировать Google API auth'
      : integrationOAuthFormMode === 'manual'
        ? 'Создать Google API auth вручную'
        : 'Импортировать Google API auth из JSON';
  }
  if (jsonWrap) jsonWrap.style.display = integrationOAuthFormMode === 'json' ? 'block' : 'none';
  if (manualWrap) manualWrap.style.display = integrationOAuthFormMode === 'manual' || integrationOAuthFormMode === 'edit' ? 'grid' : 'none';
  if (saveBtn) {
    saveBtn.textContent = integrationOAuthFormMode === 'edit'
      ? 'Сохранить auth'
      : integrationOAuthFormMode === 'manual'
        ? 'Создать auth'
        : 'Импортировать auth';
  }
  if (hint) {
    hint.textContent = integrationOAuthFormMode === 'edit'
      ? 'После сохранения список Google API auth и выбор подключения канала обновятся автоматически.'
      : 'Теперь нажмите “Подключить канал”, чтобы привязать YouTube-канал к этому Google API auth.';
  }
}

function resetIntegrationOAuthForm(mode = 'json') {
  integrationOAuthEditingProfileId = null;
  setIntegrationOAuthFormMode(mode);
  setIntegrationOAuthFormError('');
  const hasProfiles = Boolean((lastYoutubeProfiles || []).length);
  const fields = {
    'integration-oauth-name': mode === 'manual' ? 'YouTube OAuth' : 'Imported YouTube OAuth',
    'integration-oauth-json': '',
    'integration-oauth-client-id': '',
    'integration-oauth-client-secret': '',
    'integration-oauth-redirect-uri': INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI,
  };
  Object.entries(fields).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  const defaultEl = document.getElementById('integration-oauth-default');
  if (defaultEl) {
    defaultEl.checked = !hasProfiles;
    defaultEl.disabled = false;
  }
  ['integration-oauth-client-id', 'integration-oauth-client-secret', 'integration-oauth-redirect-uri'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
  const secret = document.getElementById('integration-oauth-client-secret');
  if (secret) secret.placeholder = 'client_secret';
  const saveBtn = document.getElementById('integration-oauth-save-btn');
  if (saveBtn) saveBtn.disabled = false;
}

function createIntegrationOAuthProfile(mode = 'json') {
  renderIntegrationsError('');
  resetIntegrationOAuthForm(mode);
  const modal = document.getElementById('integration-oauth-modal');
  if (modal) modal.style.display = 'grid';
  setTimeout(() => {
    const focusId = integrationOAuthFormMode === 'manual' ? 'integration-oauth-client-id' : 'integration-oauth-json';
    (document.getElementById(focusId) || document.getElementById('integration-oauth-name'))?.focus();
  }, 0);
}

function closeIntegrationOAuthModal(event) {
  if (event && event.target && event.target.id !== 'integration-oauth-modal') return;
  const modal = document.getElementById('integration-oauth-modal');
  if (modal) modal.style.display = 'none';
  integrationOAuthEditingProfileId = null;
  setIntegrationOAuthFormError('');
}

async function saveIntegrationOAuthProfile() {
  const saveBtn = document.getElementById('integration-oauth-save-btn');
  const name = (document.getElementById('integration-oauth-name')?.value || '').trim();
  const isDefault = Boolean(document.getElementById('integration-oauth-default')?.checked);
  setIntegrationOAuthFormError('');
  try {
    if (saveBtn) saveBtn.disabled = true;
    let data;
    if (integrationOAuthFormMode === 'edit') {
      const profile = settingsProfileById(integrationOAuthEditingProfileId);
      if (!profile) throw new Error('Google API auth не найден.');
      const redirectUri = (document.getElementById('integration-oauth-redirect-uri')?.value || '').trim() || profile.redirect_uri || INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI;
      const payload = {
        name: name || profile.name || `OAuth #${profile.id}`,
        redirect_uri: redirectUri,
        status: profile.status || 'active',
      };
      if (!isEnvOAuthProfile(profile)) {
        const clientId = (document.getElementById('integration-oauth-client-id')?.value || '').trim();
        const clientSecret = (document.getElementById('integration-oauth-client-secret')?.value || '').trim();
        if (!clientId) throw new Error('Укажите client_id.');
        payload.client_id = clientId;
        if (clientSecret) payload.client_secret = clientSecret;
      }
      data = await api.patch(`/api/publish/youtube/oauth-profiles/${Number(profile.id)}`, payload);
      const makeDefault = Boolean(document.getElementById('integration-oauth-default')?.checked);
      if (makeDefault && !profile.is_default) {
        data = await api.post(`/api/publish/youtube/oauth-profiles/${Number(profile.id)}/set-default`, {});
      }
    } else if (integrationOAuthFormMode === 'manual') {
      const clientId = (document.getElementById('integration-oauth-client-id')?.value || '').trim();
      const clientSecret = (document.getElementById('integration-oauth-client-secret')?.value || '').trim();
      const redirectUri = (document.getElementById('integration-oauth-redirect-uri')?.value || '').trim() || INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI;
      if (!clientId || !clientSecret) {
        throw new Error('Укажите client_id и client_secret.');
      }
      data = await api.post('/api/publish/youtube/oauth-profiles', {
        name: name || 'YouTube OAuth',
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: redirectUri,
        is_default: isDefault,
      });
    } else {
      const jsonText = (document.getElementById('integration-oauth-json')?.value || '').trim();
      if (!jsonText) {
        throw new Error('Вставьте OAuth Client JSON.');
      }
      data = await api.post('/api/publish/youtube/oauth-profiles/import-client-json', {
        name,
        json_text: jsonText,
        is_default: isDefault,
      });
    }
    if (data?.profile?.id) publishState.selectedProfileId = Number(data.profile.id);
    closeIntegrationOAuthModal();
    showToast(integrationOAuthFormMode === 'edit' ? 'Google API auth обновлён' : 'Google API auth сохранён');
    await loadIntegrationsView({silent: true});
  } catch (err) {
    const message = err.message || 'Не удалось сохранить Google API auth';
    setIntegrationOAuthFormError(message);
    renderIntegrationsError(message);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}
function editIntegrationOAuthProfile(profileId) {
  const profile = settingsProfileById(profileId);
  if (!profile) return;
  renderIntegrationsError('');
  integrationOAuthEditingProfileId = Number(profile.id);
  setIntegrationOAuthFormMode('edit');
  setIntegrationOAuthFormError('');
  const fields = {
    'integration-oauth-name': profile.name || '',
    'integration-oauth-json': '',
    'integration-oauth-client-id': profile.client_id || '',
    'integration-oauth-client-secret': '',
    'integration-oauth-redirect-uri': profile.redirect_uri || INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI,
  };
  Object.entries(fields).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  const canEditCredentials = !isEnvOAuthProfile(profile);
  ['integration-oauth-client-id', 'integration-oauth-client-secret'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = !canEditCredentials;
  });
  const redirect = document.getElementById('integration-oauth-redirect-uri');
  if (redirect) redirect.disabled = false;
  const secret = document.getElementById('integration-oauth-client-secret');
  if (secret) {
    secret.placeholder = canEditCredentials
      ? 'Оставьте пустым, чтобы не менять'
      : 'ENV Google API auth нельзя менять здесь';
  }
  const defaultEl = document.getElementById('integration-oauth-default');
  if (defaultEl) {
    defaultEl.checked = Boolean(profile.is_default);
    defaultEl.disabled = Boolean(profile.is_default);
  }
  const saveBtn = document.getElementById('integration-oauth-save-btn');
  if (saveBtn) saveBtn.disabled = false;
  const modal = document.getElementById('integration-oauth-modal');
  if (modal) modal.style.display = 'grid';
  setTimeout(() => document.getElementById('integration-oauth-name')?.focus(), 0);
}
async function setIntegrationDefaultOAuthProfile(profileId) {
  try {
    await api.post(`/api/publish/youtube/oauth-profiles/${Number(profileId)}/set-default`, {});
    publishState.selectedProfileId = Number(profileId);
    showToast('Google API auth выбран по умолчанию');
    await loadIntegrationsView({silent: true});
  } catch (err) {
    renderIntegrationsError(err.message || 'Не удалось выбрать Google API auth по умолчанию');
  }
}
async function deleteIntegrationOAuthProfile(profileId) {
  if (!confirm(`Удалить Google API auth #${profileId}?`)) return;
  try {
    await api.del(`/api/publish/youtube/oauth-profiles/${Number(profileId)}`);
    showToast('Google API auth удалён');
    await loadIntegrationsView({silent: true});
  } catch (err) {
    renderIntegrationsError(err.message || 'Не удалось удалить Google API auth');
  }
}

async function retryFailedPublishJobs() {
  const selected = selectedPublishJobList().filter(job => job.status === 'failed' || job.status === 'cancelled');
  const jobs = selected.length ? selected : getVisiblePublishJobs().filter(job => job.status === 'failed');
  if (!jobs.length) {
    showToast('Ошибочные задачи не найдены', 'err');
    return;
  }
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/jobs/bulk-retry', {job_ids: jobs.map(job => Number(job.id))});
    showToast(`Возвращено в очередь: ${data.summary?.updated || 0} · пропущено: ${data.summary?.skipped || 0}`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось повторить ошибочные задачи:\n${err.message || err}`);
  }
}

async function cancelSelectedPublishJobs() {
  const jobs = selectedPublishJobList().filter(job => job.status === 'queued' || job.status === 'failed');
  if (!jobs.length) {
    showToast('Среди выбранных нет задач в очереди или с ошибкой', 'err');
    return;
  }
  if (!confirm(`Отменить выбранные задачи публикации: ${jobs.length}?`)) return;
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
  showToast('Завершённые задачи скрыты из вида');
}

async function startYouTubeConnect() {
  const selectedProfile = getSelectedProfile();
  if (!selectedProfile) {
    const message = 'OAuth-клиент не найден. Создайте OAuth профиль в настройках.';
    publishState.onboardingHint = message;
    if (currentView === 'integrations') {
      renderIntegrationsView();
      renderIntegrationsError(message);
    } else {
      renderPublishView();
      renderPublishError(message);
    }
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
  renderIntegrationsError('');
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
    if (currentView === 'integrations') renderIntegrationsError(message);
    else renderPublishError(message);
    showToast(message, 'err');
  } finally {
    publishState.busy = false;
    renderPublishConnectButton();
  }
}

async function disconnectYouTubeAccount(accountId) {
  if (!accountId) return;
  renderPublishError('');
  renderIntegrationsError('');
  try {
    await api.post(`/api/publish/youtube/accounts/${accountId}/disconnect`, {});
    showToast('YouTube канал отключён');
    if (currentView === 'integrations') await loadIntegrationsView({silent: true});
    else if (currentView === 'storage-profile') await loadStorageProfileDetail(currentStorageProfileId);
    else await loadPublishView({silent: true});
  } catch (err) {
    const message = `Не удалось отключить канал:\n${err.message || err}`;
    if (currentView === 'integrations') renderIntegrationsError(message);
    else renderPublishError(message);
  }
}

async function syncYouTubeAccountMetadata(accountId) {
  if (!accountId) return;
  renderIntegrationsError('');
  try {
    const data = await api.post(`/api/publish/youtube/accounts/${Number(accountId)}/sync-metadata`, {});
    if (data.status === 'failed') {
      renderIntegrationsError(data.error || 'Не удалось обновить данные канала');
      showToast('Данные канала не обновлены', 'err');
    } else {
      showToast('Данные YouTube-канала обновлены');
    }
    await loadIntegrationsView({silent: true});
  } catch (err) {
    const message = err.message || 'Не удалось обновить данные канала';
    renderIntegrationsError(message);
    showToast(message, 'err');
  }
}

async function syncAllYouTubeAccountsMetadata() {
  renderIntegrationsError('');
  try {
    const data = await api.post('/api/publish/youtube/accounts/sync-metadata', {});
    const summary = data.summary || {};
    const failed = Number(summary.failed || 0);
    showToast(`Обновлено каналов: ${summary.ok || 0} · ошибок: ${failed}`, failed ? 'err' : 'ok');
    await loadIntegrationsView({silent: true});
  } catch (err) {
    const message = err.message || 'Не удалось обновить данные каналов';
    renderIntegrationsError(message);
    showToast(message, 'err');
  }
}

async function editYouTubeAccountAlias(accountId) {
  const account = (lastYoutubeAccounts || []).find(item => Number(item.id) === Number(accountId));
  if (!account) return;
  const alias = await openTextActionModal({
    title: 'Локальное название YouTube-канала',
    label: 'Alias в ShortsFarm',
    value: account.display_name || account.local_alias || account.channel_title || '',
    placeholder: account.channel_title || 'Например: anime shorts',
    hint: 'Это локальное имя для удобства. Официальное название YouTube-канала не меняется.',
    confirmText: 'Сохранить alias',
    maxLength: 160,
    validate: value => value.length > 160 ? 'Alias слишком длинный.' : '',
  });
  if (alias === null) return;
  renderIntegrationsError('');
  try {
    await api.patch(`/api/publish/youtube/accounts/${Number(accountId)}`, {local_alias: alias});
    showToast('Локальный alias сохранён');
    await loadIntegrationsView({silent: true});
  } catch (err) {
    const message = err.message || 'Не удалось сохранить alias';
    renderIntegrationsError(message);
    showToast(message, 'err');
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
  nav('integrations', document.querySelector('[data-v="integrations"]'));
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

async function loadWorkspaceSettings(options = {}) {
  const {silent = false} = options;
  try {
    const data = await api.get('/api/settings/workspace');
    const input = document.getElementById('settings-workspace-root');
    const status = document.getElementById('settings-workspace-status');
    if (input) input.value = data.workspace_root || '';
    if (status) {
      const folders = Object.keys(data.layout || {});
      status.textContent = data.workspace_root
        ? `${data.exists ? 'Workspace доступен' : 'Папка отсутствует'} · ${folders.map(name => workspaceFolderLabel(name)).join(', ')}`
        : 'workspace_root пока не настроен';
    }
    return data;
  } catch (err) {
    if (!silent) showSettingsError(err.message || 'Не удалось загрузить workspace settings');
    return null;
  }
}

async function saveWorkspaceSettings() {
  showSettingsError('');
  showSettingsOk('');
  const workspaceRoot = document.getElementById('settings-workspace-root')?.value.trim() || '';
  if (!workspaceRoot) {
    showSettingsError('Укажите абсолютный путь workspace_root.');
    return;
  }
  try {
    const data = await api.post('/api/settings/workspace', {workspace_root: workspaceRoot});
    window.ShortsFarmFiles?.setWorkspaceRoot?.(data.workspace_root, {resetPath: true});
    showSettingsOk(`Workspace сохранён: ${data.workspace_root}`);
    await loadWorkspaceSettings({silent: true});
  } catch (err) {
    showSettingsError(err.message || 'Не удалось сохранить workspace_root');
  }
}

async function pickWorkspaceDirectory() {
  showSettingsError('');
  showSettingsOk('');
  const button = document.getElementById('settings-workspace-pick-btn');
  if (button) button.disabled = true;
  try {
    const data = await api.post('/api/settings/workspace/pick-directory', {});
    if (!data.selected) return;
    const input = document.getElementById('settings-workspace-root');
    if (input) input.value = data.workspace_root || '';
    window.ShortsFarmFiles?.setWorkspaceRoot?.(data.workspace_root || null, {resetPath: true});
    await loadWorkspaceSettings({silent: true});
    showSettingsOk('Папка выбрана и workspace создан.');
  } catch (err) {
    showSettingsError(
      err.message || 'Локальный выбор папки недоступен. Укажите путь вручную.'
    );
  } finally {
    if (button) button.disabled = false;
  }
}

async function resetDatabaseFromSettings() {
  showSettingsError('');
  showSettingsOk('');
  const confirmation = document.getElementById('settings-database-confirm')?.value.trim() || '';
  const createBackup = Boolean(document.getElementById('settings-database-backup')?.checked);
  if (confirmation !== 'УДАЛИТЬ БАЗУ') {
    showSettingsError('Для удаления базы введите точную фразу: УДАЛИТЬ БАЗУ');
    return;
  }
  if (!window.confirm('Удалить всю базу ShortsFarm? Workspace-файлы останутся на диске, но записи, теги, профили и очереди будут сброшены.')) {
    return;
  }
  if (!createBackup && !window.confirm('Резервная копия выключена. Продолжить без backup?')) {
    return;
  }
  try {
    const data = await api.post('/api/settings/database/reset', {
      confirmation,
      create_backup: createBackup,
    });
    const backup = data.backup_path ? ` Backup: ${data.backup_path}` : '';
    showSettingsOk(`База удалена и создана заново.${backup} Страница перезагрузится…`);
    showToast('База сброшена');
    setTimeout(() => window.location.reload(), 900);
  } catch (err) {
    showSettingsError(err.message || 'Не удалось удалить базу');
  }
}

async function loadSettingsView(options = {}) {
  const {silent = false} = options;
  if (!silent) showSettingsError('');
  await loadWorkspaceSettings({silent: true});
  return null;
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
  const mode = document.getElementById('publish-mode')?.value || 'public';
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
  const mode = document.getElementById('publish-mode')?.value || 'public';
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
    publish_mode: document.getElementById('publish-mode')?.value || 'public',
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
    showToast(`Задача публикации #${jobId} возвращена в очередь`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось повторить задачу #${jobId}:\n${err.message || err}`);
  }
}

async function cancelPublishJob(jobId) {
  renderPublishError('');
  try {
    await api.post(`/api/publish/jobs/${jobId}/cancel`, {});
    showToast(`Задача публикации #${jobId} отменена`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось отменить задачу #${jobId}:\n${err.message || err}`);
  }
}

async function runPublishJob(jobId, force = false) {
  if (force && !confirm('Запустить эту задачу сейчас, игнорируя таймер или блокировку просрочки?')) return;
  renderPublishError('');
  try {
    await api.post(`/api/publish/jobs/${jobId}/run`, {force});
    showToast(`Задача публикации #${jobId} выполнена`);
    await refreshPublishJobs();
  } catch (err) {
    renderPublishError(`Не удалось запустить задачу #${jobId}:\n${err.message || err}`);
  }
}

async function runPublishWorkerOnce() {
  renderPublishError('');
  try {
    const data = await api.post('/api/publish/worker/run-once', {limit: 3});
    showToast(`Обработано задач публикации: ${data.processed || 0}`);
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
    if (currentView === 'integrations') {
      renderIntegrationsError(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.');
    } else if (currentView === 'publish') {
      renderPublishError(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.');
    }
  }
  loadSettingsView({silent: true});
  if (currentView === 'integrations') loadIntegrationsView({silent: true});
  else loadPublishView({silent: true});
  if (currentView === 'storage-profile') loadStorageProfileDetail(currentStorageProfileId);
}

function editingOptionalId(value) {
  return value ? Number(value) : null;
}

function studioTemplateOptions(items) {
  return (items || []).filter(item =>
    !item.deleted_at
    && (item.status || 'active') === 'active'
  );
}

function activeStudioEditingTemplates() {
  return studioTemplateOptions(editingStudioTemplates);
}

async function loadEditingSupportData() {
  const [poolsData, templatesData, profilesData, accountsData] = await Promise.all([
    api.get('/api/editing/reaction-pools').catch(() => ({items: []})),
    api.get('/api/studio/templates?status=active').catch(() => ({items: []})),
    api.get('/api/editing/channel-profiles').catch(() => ({items: []})),
    api.get('/api/publish/youtube/accounts').catch(() => ({accounts: []})),
  ]);
  editingPools = poolsData.items || [];
  editingStudioTemplates = studioTemplateOptions(templatesData.items || []);
  editingProfiles = profilesData.items || [];
  editingAccounts = accountsData.accounts || [];
  if (storageYoutubeAccounts.length) {
    const known = new Map(editingAccounts.map(account => [Number(account.id), account]));
    storageYoutubeAccounts.forEach(account => known.set(Number(account.id), account));
    editingAccounts = Array.from(known.values());
  }
}

function getVisibleEditingJobs() {
  editingJobs = lastQueueItems.filter(item => item.kind === 'render');
  const byRender = editingJobFilter === 'all'
    ? editingJobs
    : editingJobs.filter(job => job.status === editingJobFilter);
  return editingJobReviewFilter === 'all'
    ? byRender
    : byRender.filter(job => (job.review_status || 'pending') === editingJobReviewFilter);
}

function filterEditingJobs(tab, status) {
  editingJobFilter = status || 'all';
  document.querySelectorAll('[data-edit-job-filter]').forEach(item => item.classList.remove('on'));
  if (tab) tab.classList.add('on');
  renderQueueItems('jobs-table', lastQueueItems);
}

function filterEditingJobsByReview(status) {
  editingJobReviewFilter = status || 'all';
  renderQueueItems('jobs-table', lastQueueItems);
}

function toggleEditingJobPreview(jobId) {
  const id = Number(jobId);
  editingPreviewJobId = editingPreviewJobId === id ? null : id;
  renderQueueItems('jobs-table', lastQueueItems);
}

function toggleEditingJobSelection(jobId, checked) {
  const id = Number(jobId);
  if (checked) selectedEditingJobIds.add(id);
  else selectedEditingJobIds.delete(id);
  renderEditingJobSelectionState();
}

function toggleAllVisibleEditingJobs(checked) {
  getVisibleEditingJobs().forEach(job => {
    const id = editingQueueJobId(job);
    if (checked) selectedEditingJobIds.add(id);
    else selectedEditingJobIds.delete(id);
  });
  renderQueueItems('jobs-table', lastQueueItems);
}

function renderEditingJobSelectionState() {
  document.querySelectorAll('[data-editing-selected-count]').forEach(el => {
    el.textContent = selectedEditingJobIds.size
      ? `Выбрано render-задач: ${selectedEditingJobIds.size}`
      : '';
  });
}

function editingQueueJobId(job) {
  return Number(job?.job_id || job?.id || 0);
}

function selectedEditingJobs() {
  const jobs = getVisibleEditingJobs();
  return Array.from(selectedEditingJobIds)
    .map(id => jobs.find(job => editingQueueJobId(job) === Number(id)))
    .filter(Boolean);
}

async function loadEditingJobs(silent = false) {
  try {
    await loadJobs();
    editingJobs = lastQueueItems.filter(item => item.kind === 'render');
    if (
      editingPreviewJobId !== null
      && !editingJobs.some(
        job => editingQueueJobId(job) === editingPreviewJobId && job.status === 'done'
      )
    ) {
      editingPreviewJobId = null;
    }
  } catch (err) {
    if (!silent) showToast(err.message || 'Не удалось загрузить render-очередь', 'err');
  }
}

async function cancelEditingJob(jobId) {
  try {
    await api.post(`/api/editing/jobs/${Number(jobId)}/cancel`, {});
    showToast(`Render-задача #${jobId} отменена`);
    await loadEditingJobs();
  } catch (err) {
    showToast(err.message || `Не удалось отменить render-задачу #${jobId}`, 'err');
  }
}

async function retryEditingJob(jobId) {
  try {
    await api.post(`/api/editing/jobs/${Number(jobId)}/retry`, {});
    showToast(`Render-задача #${jobId} возвращена в очередь`);
    await loadEditingJobs();
  } catch (err) {
    showToast(err.message || `Не удалось повторить render-задачу #${jobId}`, 'err');
  }
}

async function renderEditingJob(jobId, force = false) {
  try {
    await api.post(`/api/editing/jobs/${Number(jobId)}/render`, {force: Boolean(force)});
    showToast(`Render-задача #${jobId} запущена`);
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || `Не удалось запустить render-задачу #${jobId}`, 'err');
    await loadEditingJobs(true);
  }
}

function editingJobReviewNote(jobId) {
  const field = document.getElementById(`editing-review-note-${Number(jobId)}`);
  if (field) return field.value;
  const job = getVisibleEditingJobs().find(item => editingQueueJobId(item) === Number(jobId));
  return job?.review_note || '';
}

function openEditingJobResult(jobId) {
  window.open(`/api/editing/jobs/${Number(jobId)}/media`, '_blank', 'noopener');
}

async function openEditingJobMpv(jobId) {
  try {
    await api.post(`/api/editing/jobs/${Number(jobId)}/open`, {});
    showToast(`Render-задача #${jobId} открыта в mpv`);
  } catch (err) {
    showToast(err.message || `Не удалось открыть render-задачу #${jobId} в mpv`, 'err');
  }
}

async function openEditingJobFolder(jobId) {
  try {
    const data = await api.get(`/api/editing/jobs/${Number(jobId)}/folder`);
    await goToOutputFolder(data.path);
  } catch (err) {
    showToast(err.message || `Не удалось открыть папку render-задачи #${jobId}`, 'err');
  }
}

async function setEditingJobReview(jobId, reviewStatus) {
  try {
    const action = reviewStatus === 'approved' ? 'approve' : 'reject';
    await api.post(`/api/editing/jobs/${Number(jobId)}/${action}`, {
      note: editingJobReviewNote(jobId),
    });
    showToast(
      reviewStatus === 'approved'
        ? `Render-задача #${jobId} одобрена`
        : `Render-задача #${jobId} отклонена`
    );
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || `Не удалось изменить проверку render-задачи #${jobId}`, 'err');
  }
}

async function resetEditingJobReview(jobId) {
  try {
    await api.post(`/api/editing/jobs/${Number(jobId)}/reset-review`, {});
    showToast(`Проверка render-задачи #${jobId} сброшена`);
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || `Не удалось сбросить проверку render-задачи #${jobId}`, 'err');
  }
}

async function runEditingWorker(limit) {
  try {
    const data = await api.post('/api/editing/worker/start', {});
    showToast(
      `Studio queue запущена · queued: ${data.queued_studio || 0} · legacy пропущено: ${data.legacy_skipped || 0}`,
      'ok'
    );
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || 'Не удалось запустить Studio queue', 'err');
  }
}

async function renderSelectedEditingJobs() {
  const jobs = selectedEditingJobs();
  if (!jobs.length) {
    showToast('Выберите render-задачи для запуска.', 'err');
    return;
  }
  try {
    const data = await api.post('/api/editing/jobs/bulk-render', {
      job_ids: jobs.map(editingQueueJobId),
      force: false,
    });
    const summary = data.summary || {};
    showToast(
      `Рендер: ${summary.processed || 0}, пропущено: ${summary.skipped || 0}, ошибок: ${summary.errors || 0}`,
      summary.errors ? 'err' : 'ok'
    );
    await loadEditingJobs(true);
  } catch (err) {
    showToast(err.message || 'Не удалось запустить выбранные render-задачи', 'err');
  }
}

async function retryFailedEditingJobs() {
  const selected = selectedEditingJobs().filter(job => ['failed','cancelled'].includes(job.status));
  const jobs = selected.length
    ? selected
    : getVisibleEditingJobs().filter(job => ['failed','cancelled'].includes(job.status));
  if (!jobs.length) {
    showToast('Нет ошибочных или отменённых render-задач для повтора.', 'err');
    return;
  }
  const results = await Promise.allSettled(
    jobs.map(job => api.post(`/api/editing/jobs/${editingQueueJobId(job)}/retry`, {}))
  );
  const errors = results.filter(result => result.status === 'rejected').length;
  showToast(`Возвращено в очередь: ${results.length - errors}, ошибок: ${errors}`, errors ? 'err' : 'ok');
  await loadEditingJobs(true);
}

async function cancelSelectedEditingJobs() {
  const jobs = selectedEditingJobs().filter(job => ['queued','failed'].includes(job.status));
  if (!jobs.length) {
    showToast('Выберите render-задачи в очереди или с ошибкой для отмены.', 'err');
    return;
  }
  const results = await Promise.allSettled(
    jobs.map(job => api.post(`/api/editing/jobs/${editingQueueJobId(job)}/cancel`, {}))
  );
  const errors = results.filter(result => result.status === 'rejected').length;
  showToast(`Отменено: ${results.length - errors}, ошибок: ${errors}`, errors ? 'err' : 'ok');
  await loadEditingJobs(true);
}

async function loadOutputs() {
  try {
    const data = await api.get('/api/outputs');
    lastOutputs = data.outputs || [];
  } catch {
    lastOutputs = [];
  }
}

Object.assign(window, {
  nav,
  toggleSidebarCollapsed,
  toggleDensity,
  setVideoViewMode,
  setClipViewMode,
  closeTextActionModal,
  confirmTextActionModal,
  closeStorageProfilePickModal,
  confirmStorageProfilePickModal,
  openInspector,
  closeInspector,
  renderActionBar,
  loadIntegrationsView,
  renderIntegrationsAccountsPanel,
  onIntegrationOAuthProfileChange,
  createIntegrationOAuthProfile,
  closeIntegrationOAuthModal,
  saveIntegrationOAuthProfile,
  editIntegrationOAuthProfile,
  setIntegrationDefaultOAuthProfile,
  deleteIntegrationOAuthProfile,
  startYouTubeConnect,
  disconnectYouTubeAccount,
  syncYouTubeAccountMetadata,
  syncAllYouTubeAccountsMetadata,
  editYouTubeAccountAlias,
  syncStorageProfileYoutubeBranding,
  toggleStorageProfileYoutubeBranding,
  setStorageProfileBrandingOverride,
  setStorageProfileTab,
  openStorageProfileDrawer,
  closeStorageProfileDrawer,
  openStorageProfileVideoPicker,
  saveStorageProfileChannelSettings,
  openQueueForChannelProfile,
  openStudioTemplate,
  openYouTubeSettings,
  openStorageProfile,
});

window.addEventListener('DOMContentLoaded', () => {
  initResponsiveShell();
  setSecs(60);
  setMode('file');
  loadDashboard();
  loadJobs();
  loadClips();
  loadOutputs();
  initFsBrowser();
  activateInitialViewFromQuery();
});
window.addEventListener('popstate', () => {
  handleProfileRouteFromLocation();
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
  if (currentView === 'queue') {
    loadJobs();
    if (queueSubView === 'clips') loadClips();
  }
  if (currentView === 'integrations') loadIntegrationsView({silent: true});
}, 5000);
