(() => {
  const state = {
    profiles: [],
    currentProfileId: null,
    currentProfile: null,
    items: [],

    activeTab: 'publish',
    drawerOpen: false,
    drawerSection: 'publish',

    selectedItemIds: new Set(),

    candidates: [],
    selectedCandidatePaths: new Set(),
    catalogSearchQuery: '',
    catalogSearchTimer: null,
    candidatePickerOpen: false,
  };

  const bridge = {
    apiGet: async () => ({}),
    apiPost: async () => ({}),
    apiPatch: async () => ({}),
    apiDel: async () => ({}),
    currentView: () => '',
    activateView: () => {},
    loadCatalogTags: async () => [],
    loadAdvancedProfileData: async () => ({}),
    pickStorageProfile: async () => null,
    workspaceMediaPathForPlayer: async path => path,
    workspaceItemByKey: () => null,
    esc: value => String(value ?? ''),
    showToast: () => {},
    showInlineError: () => {},
    hideInlineError: () => {},
    openTextActionModal: async () => null,
    tagPill: () => '',
    tagListPills: () => '<div class="mono dim">тегов пока нет</div>',
    tagOptionsHtml: () => '',
    openGlobalTagsView: () => {},
    storageAccountTitle: () => 'YouTube',
    renderBrandingFieldActions: () => '',
    renderProfilePublishControls: () => '',
    renderProfilePublishSettingsPanel: () => '',
    renderProfileChannelSettingsPanel: () => '',
    renderProfileServiceLinks: () => '',
    renderProfilePublishJobsPanel: () => '',
    renderProfileYoutubeVideosPanel: () => '',
    renderProfileErrorsPanel: () => '',
    publishBadge: item => `<span class="badge">${escapeHtml(item?.status || 'draft')}</span>`,
    publishStatus: () => '<div class="mono dim">YouTube: ещё не в очереди</div>',
    videoThumb: () => '<div class="video-thumb-placeholder"></div>',
    videoWatchThumb: () => '<div class="video-thumb-placeholder"></div>',
    webPlayerButton: () => '',
    workspaceDisplayPath: value => String(value || ''),
    workspaceFolderLabel: value => String(value || ''),
    nav: () => {},
  };

  function configure(callbacks = {}) {
    Object.assign(bridge, callbacks || {});
  }

  const STORAGE_PROFILE_VIDEO_FOLDERS = ['edits', 'ready', 'published'];

  function isStorageProfileVideoPath(path) {
    const text = String(path || '').trim();
    return STORAGE_PROFILE_VIDEO_FOLDERS.includes(text.split('/')[0]);
  }

  async function workspacePathForStorageProfile(path) {
    const relative = await bridge.workspaceMediaPathForPlayer(path);
    if (!isStorageProfileVideoPath(relative)) {
      throw new Error('В локальный профиль можно добавлять только готовые видео из Результатов монтажа, Готовых или Опубликованных.');
    }
    return relative;
  }

  function escapeHtml(value) {
    try {
      return bridge.esc(value);
    } catch {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }
  }

  function showToast(message, tone) {
    try {
      bridge.showToast(message, tone);
    } catch {}
  }

  function storageProfileById(id) {
    return state.profiles.find(profile => Number(profile.id) === Number(id)) || null;
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
      ? `<img class="storage-avatar-img" src="${escapeHtml(url)}" alt="" onerror="this.style.display='none'">`
      : '';
    return `<span class="${escapeHtml(className)} storage-avatar-wrap" style="--avatar-bg:${escapeHtml(bg)}">${image}<span class="storage-avatar-fallback">${escapeHtml(initials)}</span></span>`;
  }

  function storageProfileBannerStyle(profile) {
    const url = profile?.effective_banner_url || profile?.banner_url || '';
    const color = profile?.effective_banner_color || profile?.banner_color || '#111827';
    const safeUrl = encodeURI(String(url || '')).replace(/[()'"]/g, encodeURIComponent);
    return url
      ? `background-color:${escapeHtml(color)};background-image:linear-gradient(180deg,rgba(2,6,23,.08),rgba(2,6,23,.38)),url(&quot;${escapeHtml(safeUrl)}&quot;);`
      : `background:${escapeHtml(color)};`;
  }

  function storageProfileYoutubeLink(profile) {
    return (profile?.service_links || []).find(link => link.platform === 'youtube' && link.status === 'linked') || null;
  }

  function storageProfileErrorTarget() {
    return bridge.currentView() === 'storage-profile' ? 'storage-profile-error' : 'storage-profiles-error';
  }

  function showStorageProfileError(message) {
    bridge.showInlineError(storageProfileErrorTarget(), message);
  }

  function hideStorageProfileErrors() {
    bridge.hideInlineError('storage-profiles-error');
    bridge.hideInlineError('storage-profile-error');
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
    const active = bridge.currentView() === 'storage-profile' && Number(profile.id) === Number(state.currentProfileId);
    const youtube = storageProfileYoutubeLink(profile);
    return `<button class="storage-profile-card${active ? ' active' : ''}" onclick="selectStorageProfile(${Number(profile.id)})">
      ${storageProfileAvatarHtml(profile, 'storage-profile-icon')}
      <span class="storage-profile-card-title">${escapeHtml(storageProfileName(profile))}</span>
      <span class="mono dim">@${escapeHtml(storageProfileHandle(profile))} · ${Number(profile.item_count || 0)} видео</span>
      ${youtube ? `<span class="badge b-err"><i class="ti ti-brand-youtube"></i>${escapeHtml(youtube.display_name || 'YouTube')}</span>` : ''}
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
    </button>${state.profiles.map(storageProfileCard).join('')}`;
  }

  async function loadStorageProfiles(options = {}) {
    hideStorageProfileErrors();
    try {
      const [data] = await Promise.all([
        bridge.apiGet('/api/storage-profiles'),
        bridge.loadCatalogTags().catch(() => null),
      ]);
      state.profiles = data.items || [];
      if (options.selectId) state.currentProfileId = Number(options.selectId);
      if (state.currentProfileId && !storageProfileById(state.currentProfileId)) {
        state.currentProfileId = null;
        state.currentProfile = null;
        state.items = [];
      }
      renderStorageProfilesGrid();
      return state.profiles;
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось загрузить профили');
      return state.profiles;
    }
  }

  async function createStorageProfile() {
    const name = await bridge.openTextActionModal({
      title: 'Создать локальный профиль',
      label: 'Название профиля',
      placeholder: 'Например: Anime Shorts',
      confirmText: 'Создать профиль',
      validate: value => value ? '' : 'Введите название профиля.',
    });
    if (!name) return;
    try {
      const data = await bridge.apiPost('/api/storage-profiles', {name});
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
    state.currentProfileId = Number(profileId);
    state.candidatePickerOpen = false;
    state.activeTab = options.tab || 'publish';
    state.drawerOpen = false;
    state.drawerSection = 'publish';
    state.selectedItemIds.clear();
    hideStorageProfileErrors();
    bridge.activateView('storage-profile', document.querySelector('[data-v="storage-profiles"]'));
    setStorageProfileRoute(state.currentProfileId, {replace: Boolean(options.replace)});
    await loadStorageProfileDetail(state.currentProfileId);
  }

  async function openStorageProfilesHub(options = {}) {
    hideStorageProfileErrors();
    bridge.activateView('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
    if (!options.keepUrl) {
      const nextUrl = storageProfileUrl(null);
      if (options.replace) window.history.replaceState({}, '', nextUrl);
      else window.history.pushState({}, '', nextUrl);
    }
    await loadStorageProfiles();
  }

  async function loadStorageProfileDetail(profileId = state.currentProfileId) {
    if (!profileId) {
      renderStorageProfileDetail();
      return;
    }
    try {
      const [data] = await Promise.all([
        bridge.apiGet(`/api/storage-profiles/${Number(profileId)}`),
        bridge.loadCatalogTags().catch(() => null),
        bridge.loadAdvancedProfileData(Number(profileId)).catch(() => null),
      ]);
      state.currentProfile = data.profile;
      state.currentProfileId = Number(data.profile.id);
      state.items = data.items || [];
      state.selectedItemIds = new Set(Array.from(state.selectedItemIds).filter(id => state.items.some(item => Number(item.id) === Number(id))));
      renderStorageProfilesGrid();
      renderStorageProfileDetail();
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось загрузить профиль');
    }
  }

  function reloadCurrentProfile() {
    return loadStorageProfileDetail(state.currentProfileId);
  }

  function handleRouteFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const profileId = Number(params.get('profile') || 0);
    if (profileId) {
      openStorageProfile(profileId, {replace: true});
    } else if (bridge.currentView() === 'storage-profile') {
      openStorageProfilesHub({keepUrl: true});
    }
  }

  function setStorageProfileTab(tab) {
    const allowed = new Set(['publish', 'videos', 'queue', 'youtube', 'errors']);
    state.activeTab = allowed.has(tab) ? tab : 'publish';
    renderStorageProfileDetail();
  }

  function openStorageProfileDrawer(section = 'publish') {
    state.drawerOpen = true;
    state.drawerSection = section || 'publish';
    if (state.drawerSection === 'add-video') state.candidatePickerOpen = true;
    renderStorageProfileDetail();
  }

  function closeStorageProfileDrawer() {
    if (state.drawerSection === 'add-video') {
      state.candidatePickerOpen = false;
      state.selectedCandidatePaths.clear();
    }
    state.drawerOpen = false;
    renderStorageProfileDetail();
  }

  function storageProfileTabButton(id, label, count = null) {
    const active = state.activeTab === id;
    const countHtml = count === null || count === undefined ? '' : `<span>${Number(count) || 0}</span>`;
    return `<button class="storage-profile-tab${active ? ' active' : ''}" onclick="setStorageProfileTab('${escapeHtml(id)}')">${escapeHtml(label)}${countHtml}</button>`;
  }

  function storageProfileRulesByMode(mode) {
    return (state.currentProfile?.tag_rules || []).filter(rule => rule.mode === mode);
  }

  function storageProfileTagRulesPanel(profile) {
    const includeRules = storageProfileRulesByMode('include');
    const excludeRules = storageProfileRulesByMode('exclude');
    const includeIds = includeRules.map(rule => Number(rule.tag_id));
    const excludeIds = excludeRules.map(rule => Number(rule.tag_id));
    const matchMode = profile?.tag_match_mode || 'any';
    const includePills = includeRules.length
      ? includeRules.map(rule => `${bridge.tagPill(rule.tag, {locked: rule.locked})}${rule.locked ? '' : `<button class="tag-remove" onclick="removeStorageProfileTagRule(${Number(rule.tag_id)}, 'include')">×</button>`}`).join('')
      : '<span class="mono dim">Добавьте теги, по которым профиль будет собирать видео.</span>';
    const excludePills = excludeRules.length
      ? excludeRules.map(rule => `${bridge.tagPill(rule.tag)}<button class="tag-remove" onclick="removeStorageProfileTagRule(${Number(rule.tag_id)}, 'exclude')">×</button>`).join('')
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
            <select id="storage-profile-include-tag">${bridge.tagOptionsHtml(includeIds.concat(excludeIds))}</select>
            <button class="btn-secondary" onclick="addStorageProfileTagRule('include')">Добавить</button>
          </div>
        </div>
        <div class="storage-tag-rule-box">
          <div class="field-lbl">Exclude tags</div>
          <div class="tag-pill-list">${excludePills}</div>
          <div class="storage-tag-add">
            <select id="storage-profile-exclude-tag">${bridge.tagOptionsHtml(includeIds.concat(excludeIds))}</select>
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
    if (!state.currentProfileId) return;
    const include = next.include || storageProfileRuleIds('include');
    const exclude = next.exclude || storageProfileRuleIds('exclude');
    const mode = next.mode || document.getElementById('storage-profile-tag-match-mode')?.value || state.currentProfile?.tag_match_mode || 'any';
    try {
      const data = await bridge.apiPatch(`/api/storage-profiles/${Number(state.currentProfileId)}/tag-rules`, {
        include_tag_ids: include,
        exclude_tag_ids: exclude,
        tag_match_mode: mode,
      });
      state.currentProfile = data.profile || state.currentProfile;
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

  async function runStorageProfileTagSync() {
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/tag-sync/run`, {});
      if (data.profile) state.currentProfile = data.profile;
      if (data.items) state.items = data.items;
      renderStorageProfilesGrid();
      renderStorageProfileDetail();
      showToast(`Обновлено по тегам · добавлено: ${data.summary?.added || 0} · найдено: ${data.summary?.matched || 0}`);
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось обновить профиль по тегам');
    }
  }

  function selectedStorageProfileItems() {
    return state.items.filter(item => state.selectedItemIds.has(Number(item.id)));
  }

  function toggleStorageProfileItemSelection(itemId, checked) {
    const id = Number(itemId);
    if (checked) state.selectedItemIds.add(id);
    else state.selectedItemIds.delete(id);
    renderStorageProfileDetail();
  }

  function setAllStorageProfileItemSelection(checked) {
    state.selectedItemIds.clear();
    if (checked) {
      state.items
        .filter(item => item.file_exists && item.is_publish_ready)
        .forEach(item => state.selectedItemIds.add(Number(item.id)));
    }
    renderStorageProfileDetail();
  }

  function storageProfileLinkedYoutube() {
    return storageProfileYoutubeLink(state.currentProfile);
  }

  function storageProfileItemCard(item) {
    const title = item.title || item.file_name || item.workspace_path;
    const missing = !item.file_exists;
    const selected = state.selectedItemIds.has(Number(item.id));
    const ready = Boolean(item.is_publish_ready);
    const publishNote = ready
      ? ''
      : '<div class="mono warn storage-video-publish-status">YouTube: нужен тег «Готово»</div>';
    return `<article class="storage-video-card${missing ? ' missing' : ''}">
      <label class="storage-video-select"><input type="checkbox" ${selected ? 'checked' : ''} ${missing || !ready ? 'disabled' : ''} onchange="toggleStorageProfileItemSelection(${Number(item.id)}, this.checked)"> <span>Выбрать для YouTube</span></label>
      <div class="storage-video-thumb">${missing ? bridge.videoThumb(item.workspace_path, title) : bridge.videoWatchThumb(item.workspace_path, title)}</div>
      <div class="storage-video-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
      <div class="mono dim storage-video-path" title="${escapeHtml(item.workspace_path)}">${escapeHtml(bridge.workspaceDisplayPath(item.workspace_path))}</div>
      ${bridge.tagListPills(item.catalog_tags || [])}
      <div class="storage-video-meta">
        <span>${missing ? bridge.publishBadge({status: 'failed'}) : bridge.publishBadge(item)}</span>
        <span class="mono dim">${escapeHtml(bridge.workspaceFolderLabel(item.section || ''))}</span>
      </div>
      ${publishNote}
      ${bridge.publishStatus(item)}
      <div class="row-actions">
        ${missing ? `<button class="btn-mini" disabled title="${escapeHtml(item.path_error || 'Файл отсутствует')}">Смотреть</button>` : bridge.webPlayerButton(item.workspace_path, 'Смотреть')}
        <button class="btn-danger" onclick="removeStorageProfileItem(${Number(item.id)})">Убрать</button>
      </div>
    </article>`;
  }

  function renderStorageCandidatePicker() {
    if (!state.candidatePickerOpen) return '';
    const existing = new Set(state.items.map(item => item.workspace_path));
    const candidates = state.candidates.filter(item => !existing.has(item.workspace_path));
    const selectedCount = Array.from(state.selectedCandidatePaths).filter(path => candidates.some(item => item.workspace_path === path)).length;
    const body = candidates.length
      ? `<div class="storage-candidate-list">${candidates.map(item => {
          const checked = state.selectedCandidatePaths.has(item.workspace_path);
          return `<div class="storage-candidate-row">
          <label class="storage-video-select"><input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleStorageCandidateSelection('${escapeHtml(item.workspace_path)}', this.checked)"></label>
          ${bridge.videoWatchThumb(item.workspace_path, item.title || item.file_name)}
          <div style="min-width:0;flex:1">
            <div class="mono txt ov">${escapeHtml(item.title || item.file_name)}</div>
            <div class="mono dim ov">${escapeHtml(bridge.workspaceDisplayPath(item.workspace_path))}</div>
            ${bridge.tagListPills(item.tags || [])}
          </div>
          <button class="btn-secondary" data-path="${escapeHtml(item.workspace_path)}" onclick="addCandidateToStorageProfile(this.dataset.path)">Добавить</button>
        </div>`;
        }).join('')}</div>`
      : '<div class="empty">Начните писать название, путь или тег — результаты появятся автоматически. Можно нажать «Случайные видео».</div>';
    return `<div class="storage-candidates">
      <div class="box-head"><span>Добавить видео из каталога</span><button class="btn-mini" onclick="toggleStorageCandidatePicker()">Скрыть</button></div>
      <div class="storage-search-panel">
        <input id="storage-video-search-input" type="text" placeholder="Искать по названию, тегу или пути…" value="${escapeHtml(state.catalogSearchQuery || '')}" oninput="onStorageCatalogSearchInput(this.value)" autofocus>
        <button class="btn-secondary" onclick="loadRandomStorageCatalogVideos()">Случайные видео</button>
        <button class="btn-primary" ${selectedCount ? '' : 'disabled'} onclick="addSelectedCatalogVideosToStorageProfile()">Добавить выбранные (${selectedCount})</button>
      </div>
      ${body}
    </div>`;
  }

  function storageProfileStats() {
    const publishJobs = bridge.getPublishJobs?.() || [];
    const total = state.items.length;
    const ready = state.items.filter(item => item.file_exists && item.is_publish_ready).length;
    const blocked = state.items.filter(item => item.file_exists && !item.is_publish_ready).length;
    const missing = state.items.filter(item => !item.file_exists).length;
    const queued = publishJobs.filter(job => ['queued', 'scheduled'].includes(job.status)).length;
    const failed = publishJobs.filter(job => ['failed', 'cancelled'].includes(job.status)).length;
    const done = publishJobs.filter(job => job.status === 'done').length;
    return {total, ready, blocked, missing, queued, failed, done, selected: selectedStorageProfileItems().length};
  }

  function storageProfileMetric(label, value, tone = '') {
    return `<div class="storage-profile-metric ${escapeHtml(tone)}"><b>${Number(value) || 0}</b><span>${escapeHtml(label)}</span></div>`;
  }

  function storageProfileTabs() {
    const stats = storageProfileStats();
    const publishJobs = bridge.getPublishJobs?.() || [];
    const youtubeVideos = bridge.getYoutubeVideos?.() || [];
    return `<div class="storage-profile-tabs">
      ${storageProfileTabButton('publish', 'Публикация', stats.ready)}
      ${storageProfileTabButton('videos', 'Видео', stats.total)}
      ${storageProfileTabButton('queue', 'Очередь', publishJobs.length)}
      ${storageProfileTabButton('youtube', 'YouTube', youtubeVideos.length)}
      ${storageProfileTabButton('errors', 'Ошибки', stats.failed + stats.missing)}
    </div>`;
  }

  function storageProfileVideoGrid(items, emptyText) {
    return items.length
      ? `<div class="storage-video-grid storage-video-grid-compact">${items.map(storageProfileItemCard).join('')}</div>`
      : `<div class="empty compact">${escapeHtml(emptyText)}</div>`;
  }

  function storageProfilePublishDashboard(profile) {
    const stats = storageProfileStats();
    const ready = state.items.filter(item => item.file_exists && item.is_publish_ready);
    const waiting = state.items.filter(item => item.file_exists && !item.is_publish_ready);
    const youtube = storageProfileLinkedYoutube();
    const spotlight = ready.length ? ready : state.items.filter(item => item.file_exists).slice(0, 18);
    return `<div class="storage-profile-main-grid">
      <section class="storage-profile-cockpit">
        <div class="storage-cockpit-head">
          <div>
            <div class="storage-section-title inline-title">Готовность к публикации</div>
            <div class="mono dim">${youtube ? `Канал: ${escapeHtml(bridge.storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}` : 'YouTube-канал ещё не привязан.'}</div>
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

  function storageProfileMainContent(profile) {
    if (state.activeTab === 'videos') {
      return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Все видео профиля</div><div class="mono dim">Локальная витрина профиля. Публикация доступна только для видео с тегом «Готово».</div></div><button class="btn-secondary" onclick="openStorageProfileVideoPicker()">Добавить видео</button></div>${storageProfileVideoGrid(state.items, 'В профиле пока нет видео.')}`;
    }
    if (state.activeTab === 'queue') {
      return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Очередь публикации профиля</div><div class="mono dim">Здесь видны задачи YouTube только этого профиля.</div></div><button class="btn-mini" onclick="refreshStorageProfilePublishState()">Обновить</button></div>${bridge.renderProfilePublishJobsPanel(profile, state.items)}`;
    }
    if (state.activeTab === 'youtube') {
      return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Видео на YouTube</div><div class="mono dim">Сверка локального профиля с подключённым каналом.</div></div><button class="btn-secondary" onclick="syncStorageProfileYoutube()">Синхронизировать YouTube</button></div>${bridge.renderProfileYoutubeVideosPanel(profile, state.items)}`;
    }
    if (state.activeTab === 'errors') {
      return bridge.renderProfileErrorsPanel(profile, state.items);
    }
    return storageProfilePublishDashboard(profile);
  }

  function storageProfileLocalEditPanel(profile) {
    return `<div class="storage-profile-edit compact-panel">
      <div class="settings-meta">Локальные значения работают как ручное переопределение оформления YouTube только для изменённых полей.</div>
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Локальное название</label><input id="storage-profile-name" type="text" value="${escapeHtml(profile.name)}">${bridge.renderBrandingFieldActions(profile, 'name', 'Использовать локальное имя', 'Вернуть имя из YouTube')}</div>
        <div class="field"><label class="field-lbl">Локальный Handle</label><input id="storage-profile-handle" type="text" value="${escapeHtml(profile.handle)}">${bridge.renderBrandingFieldActions(profile, 'handle', 'Использовать локальный handle', 'Вернуть handle из YouTube')}</div>
      </div>
      <div class="field"><label class="field-lbl">Локальное описание</label><textarea id="storage-profile-description" rows="3">${escapeHtml(profile.description || '')}</textarea>${bridge.renderBrandingFieldActions(profile, 'description', 'Использовать локальное описание', 'Вернуть описание из YouTube')}</div>
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Инициалы</label><input id="storage-profile-initials" type="text" value="${escapeHtml(profile.avatar_initials || '')}"></div>
        <div class="field"><label class="field-lbl">Цвет аватара</label><input id="storage-profile-avatar-color" type="text" value="${escapeHtml(profile.avatar_color || '#3b82f6')}"></div>
        <div class="field"><label class="field-lbl">URL локального аватара</label><input id="storage-profile-avatar-url" type="text" value="${escapeHtml(profile.avatar_url || '')}">${bridge.renderBrandingFieldActions(profile, 'avatar', 'Использовать локальный аватар', 'Вернуть фото YouTube')}</div>
        <div class="field"><label class="field-lbl">Цвет баннера</label><input id="storage-profile-banner-color" type="text" value="${escapeHtml(profile.banner_color || '#111827')}"></div>
        <div class="field"><label class="field-lbl">URL локальной шапки</label><input id="storage-profile-banner-url" type="text" value="${escapeHtml(profile.banner_url || '')}">${bridge.renderBrandingFieldActions(profile, 'banner', 'Использовать локальную шапку', 'Вернуть шапку из YouTube')}</div>
      </div>
    </div>`;
  }

  function storageProfileDrawerButton(section, label) {
    const handler = section === 'add-video'
      ? 'openStorageProfileVideoPicker()'
      : `openStorageProfileDrawer('${escapeHtml(section)}')`;
    return `<button class="${state.drawerSection === section ? 'active' : ''}" onclick="${handler}">${escapeHtml(label)}</button>`;
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
    if (state.drawerSection === 'processing') return bridge.renderProfileChannelSettingsPanel(profile, state.items);
    if (state.drawerSection === 'tags') return storageProfileTagRulesPanel(profile);
    if (state.drawerSection === 'youtube') return bridge.renderProfileServiceLinks(profile, state.items);
    if (state.drawerSection === 'profile') return storageProfileLocalEditPanel(profile);
    if (state.drawerSection === 'danger') {
      return `<div class="storage-danger-panel"><b>Отключить профиль</b><p class="mono dim">Профиль исчезнет из списка, видео на диске не удаляются.</p><button class="btn-danger" onclick="disableStorageProfile()">Отключить профиль</button></div>`;
    }
    if (state.drawerSection === 'add-video') return renderStorageCandidatePicker();
    return bridge.renderProfilePublishSettingsPanel(profile, state.items);
  }

  function storageProfileDrawerFooter() {
    if (state.drawerSection === 'publish') {
      return `<button class="btn-primary" onclick="saveStorageProfilePublishSettings()">Сохранить публикацию</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
    }
    if (state.drawerSection === 'processing') {
      return `<button class="btn-primary" onclick="saveStorageProfileChannelSettings()">Сохранить обработку</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
    }
    if (state.drawerSection === 'profile') {
      return `<button class="btn-primary" onclick="saveStorageProfile()">Сохранить профиль</button><button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
    }
    return `<button class="btn-secondary" onclick="closeStorageProfileDrawer()">Закрыть</button>`;
  }

  function renderStorageProfileDrawer(profile) {
    if (!state.drawerOpen) return '';
    return `<div class="storage-profile-drawer-backdrop" onclick="closeStorageProfileDrawer()"></div>
    <aside class="storage-profile-drawer">
      <div class="storage-drawer-head">
        <div><div class="inspector-kicker">Профиль · настройки</div><h2>${escapeHtml(storageProfileDrawerTitle(state.drawerSection))}</h2></div>
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
    const profile = state.currentProfile;
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
      ? `<span class="badge b-err"><i class="ti ti-brand-youtube"></i>${escapeHtml(bridge.storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}</span>`
      : '<span class="badge">YouTube не привязан</span>';
    el.innerHTML = `<div class="storage-channel storage-channel-compact">
      <div class="storage-channel-banner compact" style="${storageProfileBannerStyle(profile)}"></div>
      <div class="storage-channel-head compact">
        ${storageProfileAvatarHtml(profile, 'storage-channel-avatar compact')}
        <div class="storage-channel-titlebox">
          <div class="storage-channel-name">${escapeHtml(effectiveName)}</div>
          <div class="storage-channel-meta mono dim">@${escapeHtml(effectiveHandle)} · ${stats.total} видео · ${stats.ready} готово · ${stats.queued} в очереди ${youtubeLabel}</div>
          <p>${escapeHtml(effectiveDescription)}</p>
        </div>
        <div class="row-actions storage-profile-head-actions">
          <button class="btn-secondary" onclick="openStorageProfileVideoPicker()">Добавить видео</button>
          <button class="btn-mini" onclick="openStorageProfileDrawer('processing')">Обработка</button>
          <button class="btn-mini" onclick="openStorageProfileDrawer('tags')">Теги</button>
          <button class="btn-mini" onclick="openStorageProfileDrawer('publish')">Настройки</button>
        </div>
      </div>
      ${storageProfileTabs()}
      ${bridge.renderProfilePublishControls(profile, state.items)}
      <div class="storage-profile-content">${storageProfileMainContent(profile)}</div>
      ${renderStorageProfileDrawer(profile)}
    </div>`;
  }

  async function saveStorageProfile() {
    if (!state.currentProfileId) return;
    const profile = state.currentProfile || {};
    try {
      const data = await bridge.apiPatch(`/api/storage-profiles/${Number(state.currentProfileId)}`, {
        name: document.getElementById('storage-profile-name')?.value ?? profile.name ?? '',
        handle: document.getElementById('storage-profile-handle')?.value ?? profile.handle ?? '',
        description: document.getElementById('storage-profile-description')?.value ?? profile.description ?? '',
        avatar_initials: document.getElementById('storage-profile-initials')?.value ?? profile.avatar_initials ?? '',
        avatar_color: document.getElementById('storage-profile-avatar-color')?.value ?? profile.avatar_color ?? '',
        avatar_url: document.getElementById('storage-profile-avatar-url')?.value ?? profile.avatar_url ?? '',
        banner_color: document.getElementById('storage-profile-banner-color')?.value ?? profile.banner_color ?? '',
        banner_url: document.getElementById('storage-profile-banner-url')?.value ?? profile.banner_url ?? '',
      });
      applyProfileUpdate(data.profile);
      showToast('Профиль сохранён');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось сохранить профиль');
    }
  }

  async function disableStorageProfile() {
    if (!state.currentProfileId) return;
    if (!confirm('Отключить локальный профиль? Видео на диске не удаляются.')) return;
    try {
      await bridge.apiDel(`/api/storage-profiles/${Number(state.currentProfileId)}`);
      state.currentProfileId = null;
      state.currentProfile = null;
      state.items = [];
      showToast('Профиль отключён');
      await openStorageProfilesHub({replace: true});
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось отключить профиль');
    }
  }

  async function searchStorageCatalogVideos(query = state.catalogSearchQuery) {
    state.catalogSearchQuery = String(query || '');
    const data = await bridge.apiGet(`/api/catalog/videos/search?q=${encodeURIComponent(state.catalogSearchQuery)}&limit=60`);
    state.candidates = data.items || [];
    state.selectedCandidatePaths = new Set(Array.from(state.selectedCandidatePaths).filter(path => state.candidates.some(item => item.workspace_path === path)));
  }

  function onStorageCatalogSearchInput(value) {
    state.catalogSearchQuery = value || '';
    if (state.catalogSearchTimer) clearTimeout(state.catalogSearchTimer);
    state.catalogSearchTimer = setTimeout(async () => {
      try {
        await searchStorageCatalogVideos(state.catalogSearchQuery);
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
      state.catalogSearchQuery = '';
      const data = await bridge.apiGet('/api/catalog/videos/random?limit=24');
      state.candidates = data.items || [];
      state.selectedCandidatePaths.clear();
      renderStorageProfileDetail();
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось загрузить случайные видео');
    }
  }

  async function toggleStorageCandidatePicker() {
    if (!state.currentProfileId) {
      showToast('Сначала выберите профиль', 'err');
      return;
    }
    if (state.drawerOpen && state.drawerSection === 'add-video') {
      closeStorageProfileDrawer();
      return;
    }
    await openStorageProfileVideoPicker();
  }

  async function openStorageProfileVideoPicker() {
    if (!state.currentProfileId) {
      showToast('Сначала выберите профиль', 'err');
      return;
    }
    state.candidatePickerOpen = true;
    state.drawerOpen = true;
    state.drawerSection = 'add-video';
    try {
      await searchStorageCatalogVideos('');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось загрузить каталог видео');
    }
    renderStorageProfileDetail();
  }

  function toggleStorageCandidateSelection(workspacePath, checked) {
    if (checked) state.selectedCandidatePaths.add(workspacePath);
    else state.selectedCandidatePaths.delete(workspacePath);
    renderStorageProfileDetail();
  }

  async function addCandidateToStorageProfile(workspacePath) {
    if (!state.currentProfileId) return;
    try {
      const candidate = state.candidates.find(item => item.workspace_path === workspacePath);
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/items`, {
        workspace_path: workspacePath,
        status: candidate?.is_publish_ready ? 'ready' : 'draft',
      });
      state.currentProfile = data.profile || state.currentProfile;
      showToast('Видео добавлено в профиль');
      state.candidatePickerOpen = false;
      state.drawerOpen = false;
      await loadStorageProfileDetail(state.currentProfileId);
      await loadStorageProfiles({selectId: state.currentProfileId});
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось добавить видео');
    }
  }

  async function addSelectedCatalogVideosToStorageProfile() {
    const paths = Array.from(state.selectedCandidatePaths);
    if (!paths.length) return;
    let added = 0;
    let errors = 0;
    for (const path of paths) {
      try {
        const candidate = state.candidates.find(item => item.workspace_path === path);
        await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/items`, {
          workspace_path: path,
          status: candidate?.is_publish_ready ? 'ready' : 'draft',
        });
        added += 1;
      } catch {
        errors += 1;
      }
    }
    state.selectedCandidatePaths.clear();
    state.candidatePickerOpen = false;
    state.drawerOpen = false;
    await loadStorageProfileDetail(state.currentProfileId);
    await loadStorageProfiles({selectId: state.currentProfileId});
    showToast(`Добавлено видео: ${added}${errors ? ` · ошибок: ${errors}` : ''}`, errors ? 'err' : 'ok');
  }

  async function removeStorageProfileItem(itemId) {
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiDel(`/api/storage-profiles/${Number(state.currentProfileId)}/items/${Number(itemId)}`);
      state.currentProfile = data.profile || state.currentProfile;
      state.items = state.items.filter(item => Number(item.id) !== Number(itemId));
      state.selectedItemIds.delete(Number(itemId));
      showToast('Видео убрано из профиля');
      renderStorageProfileDetail();
      await loadStorageProfiles({selectId: state.currentProfileId});
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось убрать видео');
    }
  }

  async function ensureStorageProfilesLoaded() {
    if (state.profiles.length) return state.profiles;
    const data = await bridge.apiGet('/api/storage-profiles');
    state.profiles = data.items || [];
    return state.profiles;
  }

  async function pickStorageProfileIdForAdd() {
    const profiles = await ensureStorageProfilesLoaded();
    if (!profiles.length) {
      bridge.nav('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
      showToast('Сначала создайте локальный профиль', 'err');
      return null;
    }
    if (state.currentProfileId && profiles.some(profile => Number(profile.id) === Number(state.currentProfileId))) {
      return Number(state.currentProfileId);
    }
    if (profiles.length === 1) return Number(profiles[0].id);
    return await bridge.pickStorageProfile(profiles);
  }

  async function addWorkspacePathToStorageProfile(path) {
    try {
      const workspacePath = await workspacePathForStorageProfile(path);
      const profileId = await pickStorageProfileIdForAdd();
      if (!profileId) return;
      await bridge.apiPost(`/api/storage-profiles/${Number(profileId)}/items`, {workspace_path: workspacePath});
      showToast('Видео добавлено в локальный профиль');
      if (bridge.currentView() === 'storage-profile' && Number(state.currentProfileId) === Number(profileId)) {
        await loadStorageProfileDetail(profileId);
      } else if (bridge.currentView() === 'storage-profiles') {
        state.currentProfileId = Number(profileId);
        await loadStorageProfiles({selectId: profileId});
      }
    } catch (err) {
      showToast(err.message || 'Не удалось добавить видео в профиль', 'err');
    }
  }

  async function addWorkspaceItemToStorageProfile(key) {
    const item = bridge.workspaceItemByKey(key);
    if (!item) return;
    await addWorkspacePathToStorageProfile(item.path || item.prepared_path || item.source_path || '');
  }

  function storageProfileWorkspaceButton(item) {
    if (!item || item.missing || !item.file_exists) {
      return `<button class="btn-mini" disabled title="${escapeHtml(item?.path_error || 'Файл отсутствует')}">В профиль</button>`;
    }
    return `<button class="btn-mini" onclick="event.stopPropagation();addWorkspaceItemToStorageProfile('${escapeHtml(item.id)}')">В профиль</button>`;
  }

  function syncCatalogVideoTags(workspacePath, tags, updatedItem = null) {
    if (!workspacePath) return;
    state.candidates = state.candidates.map(item => (
      item.workspace_path === workspacePath
        ? {...item, tags: tags || [], is_publish_ready: (tags || []).some(tag => tag.slug === 'status-ready'), ...(updatedItem || {})}
        : item
    ));
  }

  function applyProfileUpdate(profile, options = {}) {
    if (!profile) return;
    state.currentProfile = profile;
    state.currentProfileId = Number(profile.id);
    state.profiles = state.profiles.map(item => Number(item.id) === Number(profile.id) ? profile : item);
    if (!state.profiles.some(item => Number(item.id) === Number(profile.id))) state.profiles.push(profile);
    renderStorageProfilesGrid();
    if (options.render !== false) renderStorageProfileDetail();
  }

  function applyProfileDetail(profile, items = null, options = {}) {
    if (profile) applyProfileUpdate(profile, {render: false});
    if (Array.isArray(items)) {
      state.items = items;
      state.selectedItemIds = new Set(Array.from(state.selectedItemIds).filter(id => state.items.some(item => Number(item.id) === Number(id))));
    }
    if (options.render !== false) {
      renderStorageProfilesGrid();
      renderStorageProfileDetail();
    }
  }

  function setProfiles(items = []) {
    state.profiles = Array.isArray(items) ? items : [];
    if (state.currentProfileId && !storageProfileById(state.currentProfileId)) {
      state.currentProfileId = null;
      state.currentProfile = null;
      state.items = [];
    }
    renderStorageProfilesGrid();
  }

  function getCurrentProfileId() {
    return state.currentProfileId;
  }

  function getCurrentProfile() {
    return state.currentProfile;
  }

  function getCurrentItems() {
    return state.items.slice();
  }

  function getProfiles() {
    return state.profiles.slice();
  }

  function getSelectedItems() {
    return selectedStorageProfileItems();
  }

  function isItemSelected(itemId) {
    return state.selectedItemIds.has(Number(itemId));
  }

  function workspaceButtonHtml(item) {
    return storageProfileWorkspaceButton(item);
  }

  const publicApi = {
    configure,
    loadStorageProfiles,
    ensureStorageProfilesLoaded,
    loadStorageProfileDetail,
    openStorageProfile,
    openStorageProfilesHub,
    reloadCurrentProfile,
    handleRouteFromLocation,
    syncCatalogVideoTags,
    addWorkspacePathToStorageProfile,
    addWorkspaceItemToStorageProfile,
    getCurrentProfileId,
    getCurrentProfile,
    getCurrentItems,
    getProfiles,
    getSelectedItems,
    isItemSelected,
    applyProfileUpdate,
    applyProfileDetail,
    setProfiles,
    renderCurrent: renderStorageProfileDetail,
    workspaceButtonHtml,
  };

  window.ShortsFarmStorageProfiles = publicApi;

  Object.assign(window, {
    loadStorageProfiles,
    createStorageProfile,
    selectStorageProfile,
    openStorageProfile,
    openStorageProfilesHub,
    loadStorageProfileDetail,

    saveStorageProfile,
    disableStorageProfile,

    setStorageProfileTab,
    openStorageProfileDrawer,
    closeStorageProfileDrawer,
    toggleStorageProfileItemSelection,
    setAllStorageProfileItemSelection,

    saveStorageProfileTagRules,
    addStorageProfileTagRule,
    removeStorageProfileTagRule,
    runStorageProfileTagSync,

    openStorageProfileVideoPicker,
    toggleStorageCandidatePicker,
    onStorageCatalogSearchInput,
    loadRandomStorageCatalogVideos,
    toggleStorageCandidateSelection,
    addCandidateToStorageProfile,
    addSelectedCatalogVideosToStorageProfile,
    removeStorageProfileItem,

    addWorkspacePathToStorageProfile,
    addWorkspaceItemToStorageProfile,
  });
})();
