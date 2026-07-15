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

    publishJobs: [],
    youtubeVideos: [],
    youtubeAccounts: [],
  };

  const bridge = {
    apiGet: async () => ({}),
    apiPost: async () => ({}),
    apiPatch: async () => ({}),
    apiDel: async () => ({}),
    currentView: () => '',
    activateView: () => {},
    loadCatalogTags: async () => [],
    loadEditingSupportData: async () => ({}),
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
    mergePublishJobs: () => {},
    openPublishSchedule: async () => {},
    runPublishJobsNow: async () => {},
    renderPublishScheduleCell: () => '<span class="mono dim">—</span>',
    showPublishJobError: () => {},
    getEditingProfiles: () => [],
    getEditingAccounts: () => [],
    getEditingPools: () => [],
    getEditingTemplates: () => [],
    upsertEditingProfile: () => {},
    openRenderQueue: () => {},
    openStudioTemplate: () => {},
    syncGlobalYoutubeAccounts: () => {},
    badge: value => `<span class="badge">${escapeHtml(value)}</span>`,
    ruStatus: value => String(value || ''),
    shortErrorText: value => String(value || ''),
    shortPath: value => String(value || ''),
    formatMtime: value => String(value || ''),
    formatMoscowDate: value => String(value || ''),
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

  function storageAccountTitle(account) {
    if (!account) return 'YouTube канал';
    const email = account.account_email ? ` · ${account.account_email}` : '';
    return `${account.channel_title || account.display_name || `Канал #${account.id}`}${email}`;
  }

  function storageProfileServiceLinks(profile) {
    const youtube = storageProfileYoutubeLink(profile);
    const linkedAccount = youtube?.youtube_account || null;
    const selectedId = linkedAccount?.id || youtube?.external_account_id || state.youtubeAccounts[0]?.id || '';
    const accountOptions = state.youtubeAccounts.map(account => (
      `<option value="${Number(account.id)}"${Number(account.id) === Number(selectedId) ? ' selected' : ''}>${escapeHtml(storageAccountTitle(account))}</option>`
    )).join('');
    const accountControls = state.youtubeAccounts.length
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
          <div class="mono dim">${branding.synced_at ? `Успешный sync: ${escapeHtml(bridge.formatMtime(branding.synced_at))}` : 'Оформление ещё не синхронизировалось.'}${branding.attempted_at && !branding.synced_at ? ` · попытка: ${escapeHtml(bridge.formatMtime(branding.attempted_at))}` : ''}</div>
          ${branding.sync_error ? `<div class="mono err">Ошибка оформления: ${escapeHtml(bridge.shortErrorText(branding.sync_error))}</div>` : ''}
        </div>`
      : '';
    const status = youtube
      ? `<b>Привязан YouTube: ${escapeHtml(storageAccountTitle(linkedAccount) || youtube.display_name || 'канал')}</b><p>Выбирайте видео ниже: очередь, таймер и запуск публикации доступны прямо в профиле.</p>`
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
    return `<button class="btn-mini" type="button" onclick="setStorageProfileBrandingOverride('${escapeHtml(field)}', ${next})">${escapeHtml(label)}</button>`;
  }

  function storageBrandingFieldActions(profile, field, localLabel, youtubeLabel) {
    const action = storageBrandingOverrideAction(profile, field, localLabel, youtubeLabel);
    return action ? `<div class="field-actions">${action}</div>` : '';
  }

  async function linkStorageProfileYoutube() {
    if (!state.currentProfileId) return;
    const accountId = Number(document.getElementById('storage-profile-youtube-account')?.value || 0);
    if (!accountId) {
      showToast('Сначала выберите YouTube-канал', 'err');
      return;
    }
    try {
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/link`, {account_id: accountId});
      applyProfileUpdate(data.profile);
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
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiDel(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/link`);
      applyProfileUpdate(data.profile);
      showToast('YouTube-канал отвязан');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось отвязать YouTube-канал');
    }
  }

  function openStorageProfileYoutubeSettings() {
    bridge.nav('integrations', document.querySelector('[data-v="integrations"]'));
  }

  async function syncStorageProfileYoutubeBranding() {
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/sync-branding`, {});
      if (data.profile) applyProfileUpdate(data.profile);
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
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiPatch(`/api/storage-profiles/${Number(state.currentProfileId)}`, {
        youtube_branding_sync_enabled: Boolean(enabled),
      });
      applyProfileUpdate(data.profile);
      showToast(enabled ? 'Автооформление YouTube включено' : 'Автооформление YouTube выключено');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось изменить режим оформления YouTube');
    }
  }

  async function setStorageProfileBrandingOverride(field, enabled) {
    if (!state.currentProfileId) return;
    const allowed = new Set(['name', 'handle', 'description', 'avatar', 'banner']);
    if (!allowed.has(field)) return;
    try {
      const payload = {};
      payload[`${field}_override`] = Boolean(enabled);
      const data = await bridge.apiPatch(`/api/storage-profiles/${Number(state.currentProfileId)}`, payload);
      applyProfileUpdate(data.profile);
      showToast(enabled ? 'Включено локальное значение поля' : 'Поле снова берётся из YouTube');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось изменить override оформления');
    }
  }

  async function syncStorageProfileYoutube() {
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/sync`, {});
      if (data.profile || data.items) applyProfileDetail(data.profile, data.items, {render: false});
      state.publishJobs = data.jobs || state.publishJobs;
      state.youtubeVideos = data.youtube_videos || state.youtubeVideos;
      bridge.mergePublishJobs(state.publishJobs);
      renderStorageProfileDetail();
      showToast(`YouTube синхронизирован · найдено: ${data.summary?.fetched || 0} · связано: ${data.summary?.matched_profile_items || 0}`);
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось синхронизировать YouTube для профиля');
    }
  }

  function storageProfileLinkedYoutube() {
    return storageProfileYoutubeLink(state.currentProfile);
  }

  function storageProfilePublishBadge(item) {
    const job = item.publish_job;
    if (!job) return bridge.badge(item.status || 'draft');
    const schedule = job.schedule_state && job.schedule_state !== 'untimed'
      ? `<span class="schedule-state ${escapeHtml(job.schedule_state)}">${escapeHtml(bridge.ruStatus(job.schedule_state))}</span>`
      : '';
    return `${bridge.badge(job.status)}${schedule}`;
  }

  function storageProfilePublishStatus(item) {
    const job = item.publish_job;
    if (!job) return '<div class="mono dim">YouTube: ещё не в очереди</div>';
    const url = job.youtube_url
      ? `<a class="mono" href="${escapeHtml(job.youtube_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>`
      : '';
    const error = job.error ? `<button class="link-video err mono" onclick="showPublishJobError(${Number(job.id)})">${escapeHtml(bridge.shortErrorText(job.error))}</button>` : '';
    const schedule = job.upload_at ? `<div class="mono dim">загрузка: ${escapeHtml(bridge.formatMoscowDate(job.upload_at))}</div>` : '';
    return `<div class="storage-video-publish-status mono dim">YouTube job #${Number(job.id)} · ${escapeHtml(bridge.ruStatus(job.status))}${schedule}${url}${error}</div>`;
  }

  function storageProfilePublishControls() {
    const youtube = storageProfileLinkedYoutube();
    const selected = selectedStorageProfileItems().filter(item => item.file_exists && item.is_publish_ready);
    const selectedCount = selected.length;
    const disabled = !youtube || !selectedCount;
    const publishableItems = state.items.filter(item => item.file_exists && item.is_publish_ready);
    const allSelected = publishableItems.length && publishableItems.every(item => state.selectedItemIds.has(Number(item.id)));
    const note = youtube
      ? `Канал: ${escapeHtml(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}`
      : 'Сначала привяжите YouTube-канал к профилю.';
    const blocked = state.items.filter(item => item.file_exists && !item.is_publish_ready).length;
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
        <button class="btn-mini" onclick="loadStorageProfileDetail(${Number(state.currentProfileId)})">Обновить</button>
        <button class="btn-mini" onclick="openStorageProfileDrawer('publish')">Настройки</button>
      </div>
    </div>`;
  }

  function storageProfilePublishSettings() {
    const link = state.currentProfile?.service_links?.find(item => item.platform === 'youtube') || null;
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
          <input id="storage-publish-category" type="text" value="${escapeHtml(settings.category_id || '22')}">
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
        <div class="field"><label class="field-lbl">Шаблон title</label><input id="storage-publish-title-template" type="text" value="${escapeHtml(settings.title_template)}" placeholder="{title}"></div>
        <div class="field"><label class="field-lbl">Шаблон tags</label><input id="storage-publish-tags-template" type="text" value="${escapeHtml(settings.tags_template)}" placeholder="shorts, {profile}"></div>
      </div>
      <div class="field">
        <label class="field-lbl">Шаблон description</label>
        <textarea id="storage-publish-description-template" rows="3" placeholder="Описание для роликов этого профиля">${escapeHtml(settings.description_template)}</textarea>
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
    if (!state.currentProfileId) return;
    try {
      const data = await bridge.apiPatch(`/api/storage-profiles/${Number(state.currentProfileId)}/publish-settings`, readStorageProfilePublishSettingsForm());
      applyProfileUpdate(data.profile || state.currentProfile);
      showToast('Настройки публикации профиля сохранены');
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось сохранить настройки публикации профиля');
    }
  }

  function editingOptionalId(value) {
    return value ? Number(value) : null;
  }

  function storageProfileChannelProfile(profile = state.currentProfile) {
    const youtube = storageProfileYoutubeLink(profile);
    const accountId = Number(youtube?.external_account_id || youtube?.youtube_account?.id || 0);
    const effectiveName = String(storageProfileName(profile) || '').trim().toLowerCase();
    const handle = String(storageProfileHandle(profile) || '').trim().toLowerCase();
    return bridge.getEditingProfiles().find(item =>
      accountId && Number(item.youtube_account_id || 0) === accountId
    ) || bridge.getEditingProfiles().find(item => {
      const name = String(item.name || '').trim().toLowerCase();
      return name && (name === effectiveName || name === handle);
    }) || null;
  }

  function storageProfileChannelSettingsPanel(profile = state.currentProfile) {
    const channelProfile = storageProfileChannelProfile(profile);
    const youtube = storageProfileYoutubeLink(profile);
    const linkedAccountId = Number(youtube?.external_account_id || youtube?.youtube_account?.id || 0);
    const editingAccounts = bridge.getEditingAccounts();
    const selectedAccountId = Number(channelProfile?.youtube_account_id || linkedAccountId || state.youtubeAccounts[0]?.id || 0);
    const selectedTemplateId = Number(channelProfile?.default_studio_template_id || 0);
    const selectedPoolId = Number(channelProfile?.reaction_pool_id || 0);
    const accountRows = (state.youtubeAccounts.length ? state.youtubeAccounts : editingAccounts).filter(account => (account.status || 'active') === 'active');
    const accountOptions = accountRows.map(account => `<option value="${Number(account.id)}"${Number(account.id) === selectedAccountId ? ' selected' : ''}>${escapeHtml(storageAccountTitle(account))}</option>`).join('');
    const templateOptions = bridge.getEditingTemplates().map(item => {
      const id = Number(item.studio_template_id || item.id);
      return `<option value="${id}"${id === selectedTemplateId ? ' selected' : ''}>${escapeHtml(item.name || item.key)} · ${escapeHtml(item.key || '')} v${Number(item.version || 1)}</option>`;
    }).join('');
    const poolOptions = bridge.getEditingPools().map(pool => `<option value="${Number(pool.id)}"${Number(pool.id) === selectedPoolId ? ' selected' : ''}>${escapeHtml(pool.name)}${pool.item_count ? ` · ${Number(pool.item_count)}` : ''}</option>`).join('');
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
          <div class="mono dim">${escapeHtml(title)} · Studio template, пул реакций и publish defaults для render-задач этого профиля.</div>
        </div>
        <div class="row-actions">
          ${selectedTemplateId ? `<button class="btn-mini" onclick="openStudioTemplate(${selectedTemplateId})">Редактировать шаблон</button>` : ''}
          <button class="btn-mini" onclick="openQueueForChannelProfile()">Открыть render-очередь</button>
        </div>
      </div>
      ${disabledHint}
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Название channel profile</label><input id="storage-channel-profile-name" type="text" value="${escapeHtml(channelProfile?.name || storageProfileName(profile) || '')}"></div>
        <div class="field"><label class="field-lbl">YouTube аккаунт</label><select id="storage-channel-profile-account"><option value="">Без YouTube</option>${accountOptions}</select></div>
        <div class="field"><label class="field-lbl">Studio template по умолчанию</label><select id="storage-channel-profile-template"><option value="">Не выбран</option>${templateOptions}</select></div>
        <div class="field"><label class="field-lbl">Пул реакций</label><select id="storage-channel-profile-pool"><option value="">Без пула реакций</option>${poolOptions}</select></div>
        <div class="field"><label class="field-lbl">Видимость по умолчанию</label><select id="storage-channel-profile-privacy">
          <option value=""${!channelProfile?.default_privacy ? ' selected' : ''}>из настроек публикации</option>
          <option value="public"${channelProfile?.default_privacy === 'public' ? ' selected' : ''}>public</option>
          <option value="unlisted"${channelProfile?.default_privacy === 'unlisted' ? ' selected' : ''}>unlisted</option>
          <option value="private"${channelProfile?.default_privacy === 'private' ? ' selected' : ''}>private</option>
        </select></div>
        <div class="field"><label class="field-lbl">ID категории</label><input id="storage-channel-profile-category" type="text" value="${escapeHtml(channelProfile?.default_category_id || '')}" placeholder="22"></div>
      </div>
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Шаблон title</label><input id="storage-channel-profile-title-template" type="text" value="${escapeHtml(channelProfile?.title_template || '')}" placeholder="{title}"></div>
        <div class="field"><label class="field-lbl">Шаблон tags</label><input id="storage-channel-profile-tags-template" type="text" value="${escapeHtml(channelProfile?.tags_template || '')}" placeholder="shorts, {profile}"></div>
      </div>
      <div class="field"><label class="field-lbl">Шаблон description</label><textarea id="storage-channel-profile-description-template" rows="3">${escapeHtml(channelProfile?.description_template || '')}</textarea></div>
      <label class="toggle-label"><input id="storage-channel-profile-enabled" type="checkbox" ${channelProfile?.enabled === false ? '' : 'checked'}> Channel profile включён</label>
    </div>`;
  }

  function readStorageProfileChannelSettingsForm() {
    const profile = state.currentProfile;
    return {
      name: document.getElementById('storage-channel-profile-name')?.value.trim() || storageProfileName(profile) || 'Channel profile',
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
    if (!state.currentProfileId) return;
    try {
      const existing = storageProfileChannelProfile();
      const body = readStorageProfileChannelSettingsForm();
      const data = existing
        ? await bridge.apiPatch(`/api/editing/channel-profiles/${Number(existing.id)}`, body)
        : await bridge.apiPost('/api/editing/channel-profiles', body);
      if (data.item) bridge.upsertEditingProfile(data.item);
      showToast(existing ? 'Настройки обработки профиля сохранены' : 'Channel profile создан');
      renderStorageProfileDetail();
    } catch (err) {
      showStorageProfileError(err.message || 'Не удалось сохранить настройки обработки профиля');
    }
  }

  async function openQueueForChannelProfile() {
    const profile = storageProfileChannelProfile();
    const query = profile?.name || storageProfileName(state.currentProfile) || '';
    bridge.openRenderQueue(query);
  }

  function storageProfilePublishJobsPanel() {
    if (!state.publishJobs.length) {
      return '<div class="storage-profile-jobs empty">Задач публикации для этого профиля пока нет.</div>';
    }
    const rows = state.publishJobs.slice(0, 80).map(job => {
      const youtubeLink = job.youtube_url ? `<a class="btn-mini" href="${escapeHtml(job.youtube_url)}" target="_blank" rel="noopener noreferrer">YouTube</a>` : '—';
      const actions = [];
      if (job.can_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)})">Запустить</button>`);
      else if (job.can_force_run) actions.push(`<button class="btn-mini" onclick="runPublishJob(${Number(job.id)}, true)">Запустить сейчас</button>`);
      if (job.can_retry) actions.push(`<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Повторить</button>`);
      if (job.can_cancel) actions.push(`<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Отменить</button>`);
      if (job.status === 'queued') actions.push(`<button class="btn-secondary" onclick="openStorageProfileScheduleForJobs([${Number(job.id)}])">Таймер</button>`);
      return `<tr><td class="mono dim">#${job.id}</td><td>${bridge.badge(job.status)}</td><td class="mono mid ov">${escapeHtml(job.title || '—')}</td><td>${bridge.renderPublishScheduleCell(job)}</td><td class="mono dim ov">${escapeHtml(bridge.shortPath(job.clip_output_path || '—'))}</td><td>${youtubeLink}</td><td><div class="row-actions">${actions.join('')}</div></td></tr>`;
    }).join('');
    return `<div class="storage-profile-jobs"><table class="tbl compact"><thead><tr><th>#</th><th>Статус</th><th>Видео</th><th>Таймер</th><th>Файл</th><th>YouTube</th><th>Действие</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function storageProfileYoutubeVideoCard(video) {
    const thumb = video.thumbnail_url
      ? `<img src="${escapeHtml(video.thumbnail_url)}" alt="">`
      : `<div class="video-thumb-placeholder"><i class="ti ti-brand-youtube"></i></div>`;
    const title = video.title || video.external_video_id || 'YouTube video';
    const matched = video.matched
      ? '<span class="badge b-ok">связано с профилем</span>'
      : '<span class="badge">только на YouTube</span>';
    const privacy = video.privacy_status ? bridge.badge(video.privacy_status) : '';
    const published = video.published_at ? `<div class="mono dim">опубликовано: ${escapeHtml(bridge.formatMtime(video.published_at))}</div>` : '';
    const local = video.profile_item_workspace_path ? `<div class="mono dim ov">${escapeHtml(bridge.workspaceDisplayPath(video.profile_item_workspace_path))}</div>` : '';
    return `<article class="storage-youtube-video-card">
      <div class="storage-youtube-thumb">${thumb}</div>
      <div class="storage-video-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
      <div class="storage-video-meta">${matched}${privacy}</div>
      ${published}
      ${local}
      <div class="row-actions">
        <a class="btn-mini" href="${escapeHtml(video.external_url)}" target="_blank" rel="noopener noreferrer">Открыть YouTube</a>
      </div>
    </article>`;
  }

  function storageProfileYoutubeVideosPanel() {
    const youtube = storageProfileLinkedYoutube();
    const link = state.currentProfile?.service_links?.find(item => item.platform === 'youtube');
    const syncInfo = link?.last_sync_at
      ? `Последняя синхронизация YouTube: ${bridge.formatMtime(link.last_sync_at)} · найдено видео: ${Number(link.synced_video_count || state.youtubeVideos.length || 0)}`
      : 'Полной синхронизации с YouTube ещё не было.';
    const error = link?.last_sync_error ? `<div class="mono err">Ошибка sync: ${escapeHtml(link.last_sync_error)}</div>` : '';
    if (!youtube) {
      return '<div class="storage-youtube-inventory empty">Привяжите YouTube-канал, чтобы сверить профиль с реальным каналом.</div>';
    }
    const body = state.youtubeVideos.length
      ? `<div class="storage-youtube-grid">${state.youtubeVideos.map(storageProfileYoutubeVideoCard).join('')}</div>`
      : '<div class="empty">Видео YouTube ещё не загружены. Нажмите «Синхронизировать YouTube».</div>';
    return `<div class="storage-youtube-inventory">
      <div class="mono dim">${escapeHtml(syncInfo)}</div>
      ${error}
      ${body}
    </div>`;
  }

  function storageProfileErrorsPanel(profile = state.currentProfile, items = state.items) {
    const missing = (items || []).filter(item => item.missing || !item.file_exists);
    const failedJobs = (state.publishJobs || []).filter(job => job.status === 'failed' || job.error);
    const missingRows = missing.length
      ? `<div class="storage-profile-jobs"><table class="tbl compact"><thead><tr><th>Видео</th><th>Путь</th><th>Действие</th></tr></thead><tbody>${missing.map(item => (
          `<tr>
            <td class="mono mid ov">${escapeHtml(item.title || item.file_name || item.workspace_path || `#${item.id}`)}</td>
            <td class="mono dim ov">${escapeHtml(bridge.workspaceDisplayPath(item.workspace_path || item.path || '—'))}</td>
            <td><button class="btn-danger" onclick="removeStorageProfileItem(${Number(item.id)})">Убрать из профиля</button></td>
          </tr>`
        )).join('')}</tbody></table></div>`
      : '<div class="empty compact">Missing-файлов в профиле нет.</div>';
    const failedRows = failedJobs.length
      ? `<div class="storage-profile-jobs"><table class="tbl compact"><thead><tr><th>#</th><th>Статус</th><th>Видео</th><th>Ошибка</th><th>Действие</th></tr></thead><tbody>${failedJobs.map(job => {
          const actions = [];
          if (job.can_retry) actions.push(`<button class="btn-mini" onclick="retryPublishJob(${Number(job.id)})">Повторить</button>`);
          if (job.can_cancel) actions.push(`<button class="btn-danger" onclick="cancelPublishJob(${Number(job.id)})">Отменить</button>`);
          return `<tr>
            <td class="mono dim">#${Number(job.id)}</td>
            <td>${bridge.badge(job.status || 'failed')}</td>
            <td class="mono mid ov">${escapeHtml(job.title || job.clip_output_path || '—')}</td>
            <td><button class="link-video err mono" onclick="showPublishJobError(${Number(job.id)})">${escapeHtml(bridge.shortErrorText(job.error || 'ошибка публикации'))}</button></td>
            <td><div class="row-actions">${actions.join('')}</div></td>
          </tr>`;
        }).join('')}</tbody></table></div>`
      : '<div class="empty compact">Ошибок публикации для этого профиля нет.</div>';
    const brandingError = profile?.youtube_branding?.sync_error
      ? `<div class="storage-service-note"><i class="ti ti-alert-triangle"></i><div><b>Ошибка оформления YouTube</b><p class="mono err">${escapeHtml(bridge.shortErrorText(profile.youtube_branding.sync_error))}</p><button class="btn-secondary" onclick="syncStorageProfileYoutubeBranding()">Повторить sync оформления</button></div></div>`
      : '';
    return `<div class="storage-profile-errors">
      <div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Ошибки и missing</div><div class="mono dim">Проблемы файлов, оформления и YouTube-задач только этого профиля.</div></div><button class="btn-mini" onclick="refreshStorageProfilePublishState()">Обновить</button></div>
      ${brandingError}
      <div class="storage-section-title inline-title">Missing / удалённые файлы</div>
      ${missingRows}
      <div class="storage-section-title inline-title">Ошибки YouTube-очереди</div>
      ${failedRows}
    </div>`;
  }

  async function refreshStorageProfilePublishState() {
    if (!state.currentProfileId) return;
    const [profileData, jobsData, youtubeVideosData] = await Promise.all([
      bridge.apiGet(`/api/storage-profiles/${Number(state.currentProfileId)}`),
      bridge.apiGet(`/api/storage-profiles/${Number(state.currentProfileId)}/publish-jobs?limit=200`),
      bridge.apiGet(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/videos?limit=200`).catch(() => ({videos: []})),
    ]);
    state.publishJobs = jobsData.jobs || [];
    state.youtubeVideos = youtubeVideosData.videos || [];
    bridge.mergePublishJobs(state.publishJobs);
    applyProfileDetail(profileData.profile, profileData.items);
  }

  async function enqueueStorageProfileSelection(mode = 'queue') {
    if (!state.currentProfileId) return;
    const publishSettings = readStorageProfilePublishSettingsForm();
    const selected = selectedStorageProfileItems().filter(item => item.file_exists && item.is_publish_ready);
    if (!selected.length) {
      showToast('Выберите видео профиля с тегом «Готово»', 'err');
      return;
    }
    try {
      const data = await bridge.apiPost(`/api/storage-profiles/${Number(state.currentProfileId)}/youtube/enqueue`, {
        item_ids: selected.map(item => Number(item.id)),
        publish_mode: publishSettings.publish_mode,
        category_id: publishSettings.category_id,
        made_for_kids: publishSettings.made_for_kids,
        title_template: publishSettings.title_template,
        description_template: publishSettings.description_template,
        tags_template: publishSettings.tags_template,
      });
      if (data.profile || data.profile_items) applyProfileDetail(data.profile, data.profile_items, {render: false});
      bridge.mergePublishJobs(data.jobs || []);
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
    await bridge.openPublishSchedule(ids, state.publishJobs);
  }

  async function runStorageProfilePublishJobsNow(jobIds) {
    const ids = (jobIds || []).map(Number).filter(Boolean);
    if (!ids.length) {
      showToast('Нет задач для запуска', 'err');
      return;
    }
    await bridge.runPublishJobsNow(ids);
    await refreshStorageProfilePublishState();
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

  async function loadStorageYoutubeAccounts() {
    const data = await bridge.apiGet('/api/publish/youtube/accounts');
    state.youtubeAccounts = (data.accounts || []).filter(account => (account.status || 'active') === 'active');
    bridge.syncGlobalYoutubeAccounts(data.accounts || []);
    return state.youtubeAccounts;
  }

  async function loadStorageProfileDetail(profileId = state.currentProfileId) {
    if (!profileId) {
      renderStorageProfileDetail();
      return;
    }
    try {
      const [data, jobsData, youtubeVideosData] = await Promise.all([
        bridge.apiGet(`/api/storage-profiles/${Number(profileId)}`),
        bridge.apiGet(`/api/storage-profiles/${Number(profileId)}/publish-jobs?limit=200`).catch(() => ({jobs: []})),
        bridge.apiGet(`/api/storage-profiles/${Number(profileId)}/youtube/videos?limit=200`).catch(() => ({videos: []})),
        loadStorageYoutubeAccounts().catch(() => []),
        bridge.loadCatalogTags().catch(() => null),
        bridge.loadEditingSupportData().catch(() => null),
      ]);
      state.currentProfile = data.profile;
      state.currentProfileId = Number(data.profile.id);
      state.items = data.items || [];
      state.publishJobs = jobsData.jobs || [];
      state.youtubeVideos = youtubeVideosData.videos || [];
      bridge.mergePublishJobs(state.publishJobs);
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
        <span>${missing ? bridge.badge('failed') : storageProfilePublishBadge(item)}</span>
        <span class="mono dim">${escapeHtml(bridge.workspaceFolderLabel(item.section || ''))}</span>
      </div>
      ${publishNote}
      ${storageProfilePublishStatus(item)}
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
    const total = state.items.length;
    const ready = state.items.filter(item => item.file_exists && item.is_publish_ready).length;
    const blocked = state.items.filter(item => item.file_exists && !item.is_publish_ready).length;
    const missing = state.items.filter(item => !item.file_exists).length;
    const queued = state.publishJobs.filter(job => ['queued', 'scheduled'].includes(job.status)).length;
    const failed = state.publishJobs.filter(job => ['failed', 'cancelled'].includes(job.status)).length;
    const done = state.publishJobs.filter(job => job.status === 'done').length;
    return {total, ready, blocked, missing, queued, failed, done, selected: selectedStorageProfileItems().length};
  }

  function storageProfileMetric(label, value, tone = '') {
    return `<div class="storage-profile-metric ${escapeHtml(tone)}"><b>${Number(value) || 0}</b><span>${escapeHtml(label)}</span></div>`;
  }

  function storageProfileTabs() {
    const stats = storageProfileStats();
    return `<div class="storage-profile-tabs">
      ${storageProfileTabButton('publish', 'Публикация', stats.ready)}
      ${storageProfileTabButton('videos', 'Видео', stats.total)}
      ${storageProfileTabButton('queue', 'Очередь', state.publishJobs.length)}
      ${storageProfileTabButton('youtube', 'YouTube', state.youtubeVideos.length)}
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
            <div class="mono dim">${youtube ? `Канал: ${escapeHtml(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}` : 'YouTube-канал ещё не привязан.'}</div>
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
      return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Очередь публикации профиля</div><div class="mono dim">Здесь видны задачи YouTube только этого профиля.</div></div><button class="btn-mini" onclick="refreshStorageProfilePublishState()">Обновить</button></div>${storageProfilePublishJobsPanel(profile, state.items)}`;
    }
    if (state.activeTab === 'youtube') {
      return `<div class="storage-feed-head padded"><div><div class="storage-section-title inline-title">Видео на YouTube</div><div class="mono dim">Сверка локального профиля с подключённым каналом.</div></div><button class="btn-secondary" onclick="syncStorageProfileYoutube()">Синхронизировать YouTube</button></div>${storageProfileYoutubeVideosPanel(profile, state.items)}`;
    }
    if (state.activeTab === 'errors') {
      return storageProfileErrorsPanel(profile, state.items);
    }
    return storageProfilePublishDashboard(profile);
  }

  function storageProfileLocalEditPanel(profile) {
    return `<div class="storage-profile-edit compact-panel">
      <div class="settings-meta">Локальные значения работают как ручное переопределение оформления YouTube только для изменённых полей.</div>
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Локальное название</label><input id="storage-profile-name" type="text" value="${escapeHtml(profile.name)}">${storageBrandingFieldActions(profile, 'name', 'Использовать локальное имя', 'Вернуть имя из YouTube')}</div>
        <div class="field"><label class="field-lbl">Локальный Handle</label><input id="storage-profile-handle" type="text" value="${escapeHtml(profile.handle)}">${storageBrandingFieldActions(profile, 'handle', 'Использовать локальный handle', 'Вернуть handle из YouTube')}</div>
      </div>
      <div class="field"><label class="field-lbl">Локальное описание</label><textarea id="storage-profile-description" rows="3">${escapeHtml(profile.description || '')}</textarea>${storageBrandingFieldActions(profile, 'description', 'Использовать локальное описание', 'Вернуть описание из YouTube')}</div>
      <div class="field-grid">
        <div class="field"><label class="field-lbl">Инициалы</label><input id="storage-profile-initials" type="text" value="${escapeHtml(profile.avatar_initials || '')}"></div>
        <div class="field"><label class="field-lbl">Цвет аватара</label><input id="storage-profile-avatar-color" type="text" value="${escapeHtml(profile.avatar_color || '#3b82f6')}"></div>
        <div class="field"><label class="field-lbl">URL локального аватара</label><input id="storage-profile-avatar-url" type="text" value="${escapeHtml(profile.avatar_url || '')}">${storageBrandingFieldActions(profile, 'avatar', 'Использовать локальный аватар', 'Вернуть фото YouTube')}</div>
        <div class="field"><label class="field-lbl">Цвет баннера</label><input id="storage-profile-banner-color" type="text" value="${escapeHtml(profile.banner_color || '#111827')}"></div>
        <div class="field"><label class="field-lbl">URL локальной шапки</label><input id="storage-profile-banner-url" type="text" value="${escapeHtml(profile.banner_url || '')}">${storageBrandingFieldActions(profile, 'banner', 'Использовать локальную шапку', 'Вернуть шапку из YouTube')}</div>
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
    if (state.drawerSection === 'processing') return storageProfileChannelSettingsPanel(profile, state.items);
    if (state.drawerSection === 'tags') return storageProfileTagRulesPanel(profile);
    if (state.drawerSection === 'youtube') return storageProfileServiceLinks(profile, state.items);
    if (state.drawerSection === 'profile') return storageProfileLocalEditPanel(profile);
    if (state.drawerSection === 'danger') {
      return `<div class="storage-danger-panel"><b>Отключить профиль</b><p class="mono dim">Профиль исчезнет из списка, видео на диске не удаляются.</p><button class="btn-danger" onclick="disableStorageProfile()">Отключить профиль</button></div>`;
    }
    if (state.drawerSection === 'add-video') return renderStorageCandidatePicker();
    return storageProfilePublishSettingsPanel(profile, state.items);
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
      ? `<span class="badge b-err"><i class="ti ti-brand-youtube"></i>${escapeHtml(storageAccountTitle(youtube.youtube_account) || youtube.display_name || 'YouTube')}</span>`
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
      ${storageProfilePublishControls(profile, state.items)}
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

  async function openLinkedProfile(youtubeAccountId, channelProfileName = '') {
    try {
      const profiles = await ensureStorageProfilesLoaded();
      const accountId = Number(youtubeAccountId || 0);
      const name = String(channelProfileName || '').trim().toLowerCase();
      const profile = profiles.find(item =>
        accountId
        && (item.service_links || []).some(link =>
          link.platform === 'youtube'
          && Number(link.external_account_id || 0) === accountId
        )
      ) || profiles.find(item =>
        name && String(item.effective_name || item.name || '').trim().toLowerCase() === name
      );
      if (profile) {
        await openStorageProfile(Number(profile.id));
        return profile;
      }
      bridge.nav('storage-profiles', document.querySelector('[data-v="storage-profiles"]'));
      showToast('Локальный профиль для этой задачи не найден. Открыл раздел «Профили».');
      return null;
    } catch (err) {
      showToast(err.message || 'Не удалось открыть профиль', 'err');
      return null;
    }
  }

  function getYoutubeAccounts() {
    return state.youtubeAccounts.slice();
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
        ? {
            ...item,
            tags: tags || [],
            title: updatedItem?.title !== undefined ? (updatedItem.title || item.title) : item.title,
            workspace_status: updatedItem?.workspace_status || item.workspace_status,
            is_publish_ready: (tags || []).some(tag => tag.slug === 'status-ready'),
          }
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

  function workspaceButtonHtml(item) {
    return storageProfileWorkspaceButton(item);
  }

  const publicApi = {
    configure,
    loadStorageProfiles,
    loadStorageProfileDetail,
    openStorageProfile,
    openStorageProfilesHub,
    reloadCurrentProfile,
    handleRouteFromLocation,
    syncCatalogVideoTags,
    addWorkspacePathToStorageProfile,
    addWorkspaceItemToStorageProfile,
    workspaceButtonHtml,
    openLinkedProfile,
    getYoutubeAccounts,
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

    linkStorageProfileYoutube,
    unlinkStorageProfileYoutube,
    openStorageProfileYoutubeSettings,
    syncStorageProfileYoutubeBranding,
    toggleStorageProfileYoutubeBranding,
    setStorageProfileBrandingOverride,
    syncStorageProfileYoutube,

    saveStorageProfilePublishSettings,
    refreshStorageProfilePublishState,
    enqueueStorageProfileSelection,
    openStorageProfileScheduleForJobs,
    runStorageProfilePublishJobsNow,

    saveStorageProfileChannelSettings,
    openQueueForChannelProfile,
  });
})();
