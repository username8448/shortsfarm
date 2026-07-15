(function () {
  'use strict';

  const INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI = 'http://127.0.0.1:8000/api/publish/youtube/oauth/callback';

  const state = {
    accounts: [],
    oauthProfiles: [],
    selectedOAuthProfileId: null,
    searchQuery: '',
    oauthFormMode: 'json',
    oauthEditingProfileId: null,
    connectBusy: false,
    loaded: false,
  };

  const bridge = {
    apiGet: async () => ({}),
    apiPost: async () => ({}),
    apiPatch: async () => ({}),
    apiDel: async () => ({}),
    badge: value => String(value || ''),
    currentView: () => '',
    esc: value => String(value ?? ''),
    formatMtime: value => value || '—',
    hideInlineError: () => {},
    loadSettingsView: async () => {},
    nav: () => {},
    openStorageProfile: () => {},
    openTextActionModal: async () => null,
    refreshPublishView: async () => {},
    reloadStorageProfile: async () => {},
    renderPublishConnectButton: () => {},
    renderPublishError: () => {},
    shortErrorText: value => String(value || ''),
    shortPath: value => String(value || ''),
    showInlineError: () => {},
    showToast: () => {},
    syncPublishSelections: () => {},
    getPublishSelectedOAuthProfileId: () => null,
  };

  function configure(options = {}) {
    Object.assign(bridge, options || {});
  }

  function esc(value) {
    return bridge.esc(value);
  }

  function copyItems(items) {
    return Array.isArray(items) ? items.slice() : [];
  }

  function activeOAuthProfiles() {
    return state.oauthProfiles.filter(profile => (profile.status || 'active') === 'active');
  }

  function getOAuthProfiles() {
    return copyItems(state.oauthProfiles);
  }

  function getActiveOAuthProfiles() {
    return activeOAuthProfiles().slice();
  }

  function getAccounts() {
    return copyItems(state.accounts);
  }

  function getOAuthProfileById(profileId) {
    return state.oauthProfiles.find(profile => Number(profile.id) === Number(profileId)) || null;
  }

  function isEnvProfile(profile) {
    return profile?.mode === 'env';
  }

  function profileSourceLabel(profile) {
    if (!profile) return '—';
    if (isEnvProfile(profile)) return 'окружение';
    if (profile.mode === 'legacy') return 'legacy settings';
    return 'ручной профиль';
  }

  function selectedOAuthProfile() {
    return activeOAuthProfiles().find(profile => Number(profile.id) === Number(state.selectedOAuthProfileId)) || null;
  }

  function reconcileSelectedOAuthProfile(preferredId = null) {
    const profiles = activeOAuthProfiles();
    const preferred = preferredId !== null && preferredId !== undefined ? Number(preferredId) : Number(state.selectedOAuthProfileId);
    if (profiles.some(profile => Number(profile.id) === preferred)) {
      state.selectedOAuthProfileId = preferred;
      return selectedOAuthProfile();
    }
    const fallback = profiles.find(profile => profile.is_default) || profiles[0] || null;
    state.selectedOAuthProfileId = fallback ? Number(fallback.id) : null;
    return fallback;
  }

  function renderIntegrationsError(message) {
    if (message) bridge.showInlineError('integrations-error', message);
    else bridge.hideInlineError('integrations-error');
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
    const profiles = activeOAuthProfiles();
    if (!profiles.length) {
      state.selectedOAuthProfileId = null;
      select.innerHTML = '<option value="">OAuth profile не найден</option>';
      select.disabled = true;
      if (meta) meta.innerHTML = '<div>Создайте или импортируйте Google API auth слева.</div>';
      return;
    }
    reconcileSelectedOAuthProfile();
    select.disabled = false;
    select.innerHTML = profiles.map(profile => {
      const suffix = [profile.is_default ? 'по умолчанию' : '', profileSourceLabel(profile)].filter(Boolean).join(' · ');
      return `<option value="${Number(profile.id)}"${Number(profile.id) === Number(state.selectedOAuthProfileId) ? ' selected' : ''}>${esc(profile.name || `OAuth #${profile.id}`)}${suffix ? ` · ${esc(suffix)}` : ''}</option>`;
    }).join('');
    const selected = selectedOAuthProfile();
    if (meta && selected) {
      meta.innerHTML = `<div>${esc(profileSourceLabel(selected))} · ${selected.client_secret_set ? 'secret сохранён' : 'secret не задан'}</div><div>Redirect URI: <span class="mono">${esc(selected.redirect_uri || '—')}</span></div>`;
    }
  }

  function renderIntegrationsConnectState() {
    const el = document.getElementById('integrations-connect-state');
    if (!el) return;
    const profiles = activeOAuthProfiles();
    if (!profiles.length) {
      el.innerHTML = `<div class="setup-panel">${bridge.badge('error')} <span class="mono txt">Нет активного Google API auth</span><p>Импортируйте OAuth Client JSON или создайте auth вручную.</p></div>`;
      return;
    }
    const accountCount = state.accounts.filter(account => (account.status || 'active') === 'active').length;
    el.innerHTML = `<div class="setup-panel">${bridge.badge('active')} <span class="mono txt">Готово к подключению YouTube</span><p>Подключённых каналов: ${accountCount}. Выберите Google API auth и нажмите «Подключить канал».</p></div>`;
  }

  function renderIntegrationsOAuthProfilesPanel() {
    const el = document.getElementById('integrations-oauth-profiles');
    if (!el) return;
    const rows = state.oauthProfiles;
    if (!rows.length) {
      el.innerHTML = '<div class="empty">Google API auth пока нет. Импортируйте JSON OAuth-клиента или создайте профиль вручную.</div>';
      return;
    }
    el.innerHTML = `<table class="tbl compact"><thead><tr><th>#</th><th>Название</th><th>Client</th><th>Redirect</th><th>Статус</th><th>Действие</th></tr></thead><tbody>${rows.map(profile => {
      const mode = `${profileSourceLabel(profile)}${profile.is_default ? ' · по умолчанию' : ''}`;
      const actions = [
        `<button class="btn-mini" onclick="editIntegrationOAuthProfile(${Number(profile.id)})">Редактировать</button>`,
        profile.is_default ? '' : `<button class="btn-mini" onclick="setIntegrationDefaultOAuthProfile(${Number(profile.id)})">По умолчанию</button>`,
        isEnvProfile(profile) ? '' : `<button class="btn-danger" onclick="deleteIntegrationOAuthProfile(${Number(profile.id)})">Удалить</button>`,
      ].filter(Boolean).join('');
      return `<tr><td class="mono dim">#${profile.id}</td><td><div class="mono txt">${esc(profile.name || `OAuth #${profile.id}`)}</div><div class="mono dim">${esc(mode)}</div></td><td class="mono dim ov">${esc(profile.client_id || '—')}</td><td class="mono dim ov">${esc(profile.redirect_uri || '—')}</td><td>${bridge.badge(profile.status || 'active')}</td><td><div class="row-actions">${actions}</div></td></tr>`;
    }).join('')}</tbody></table>`;
  }

  function renderIntegrationsAccountsPanel(searchValue = null) {
    const el = document.getElementById('integrations-accounts-list');
    if (!el) return;
    const input = document.getElementById('integrations-search');
    if (searchValue !== null && searchValue !== undefined) {
      state.searchQuery = String(searchValue || '').trim().toLowerCase();
      if (input) input.value = searchValue;
    } else {
      state.searchQuery = (input?.value || state.searchQuery || '').trim().toLowerCase();
    }
    const accounts = state.accounts.filter(account => !state.searchQuery || integrationAccountSearchText(account).includes(state.searchQuery));
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
        ? linked.map(profile => `<button class="link-video mono" onclick="ShortsFarmIntegrations.openStorageProfile(${Number(profile.id)})">${esc(profile.name || `Профиль #${profile.id}`)}</button>`).join('<span class="mono dim">, </span>')
        : '<span class="mono dim">не привязан</span>';
      const disabled = account.status === 'disconnected';
      const accountError = account.error ? `<div class="mono err">${esc(bridge.shortErrorText(account.error))}</div>` : '';
      const syncError = account.metadata_sync_error ? `<div class="mono err" title="${esc(account.metadata_sync_error)}">${esc(bridge.shortErrorText(account.metadata_sync_error))}</div>` : '';
      const description = account.channel_description ? `<div class="mono dim ov" title="${esc(account.channel_description)}">${esc(account.channel_description)}</div>` : '';
      const published = account.channel_published_at ? `<span>создан: ${esc(bridge.formatMtime(account.channel_published_at))}</span>` : '';
      const country = account.channel_country ? `<span>страна: ${esc(account.channel_country)}</span>` : '';
      const syncMeta = [
        account.metadata_synced_at ? `sync: ${bridge.formatMtime(account.metadata_synced_at)}` : 'sync: ещё не было',
        account.uploads_playlist_id ? `uploads: ${bridge.shortPath(account.uploads_playlist_id)}` : '',
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
        <td><div>${bridge.badge(account.status || 'active')}</div><div class="mono dim">${esc(syncMeta)}</div>${syncError}</td>
        <td><div class="row-actions">
          <button class="btn-mini" onclick="syncYouTubeAccountMetadata(${Number(account.id)})">Обновить данные</button>
          <button class="btn-mini" onclick="editYouTubeAccountAlias(${Number(account.id)})">Alias</button>
          <button class="btn-danger" ${disabled ? 'disabled' : ''} onclick="disconnectYouTubeAccount(${Number(account.id)})">Отключить</button>
        </div></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  }

  function renderIntegrationsView() {
    bridge.renderPublishConnectButton();
    renderIntegrationsOAuthSelect();
    renderIntegrationsConnectState();
    renderIntegrationsOAuthProfilesPanel();
    renderIntegrationsAccountsPanel();
    renderIntegrationsConnectButton();
  }

  function renderIntegrationsConnectButton() {
    const btn = document.getElementById('integrations-connect-btn');
    if (!btn) return;
    btn.disabled = state.connectBusy || !selectedOAuthProfile();
    btn.innerHTML = '<i class="ti ti-brand-youtube"></i> Подключить канал';
  }

  async function refreshData(options = {}) {
    const {render = true} = options;
    const [profilesData, accountsData] = await Promise.all([
      bridge.apiGet('/api/publish/youtube/oauth-profiles'),
      bridge.apiGet('/api/publish/youtube/accounts'),
    ]);
    state.oauthProfiles = profilesData.profiles || [];
    state.accounts = accountsData.accounts || [];
    state.loaded = true;
    reconcileSelectedOAuthProfile();
    bridge.syncPublishSelections();
    if (render && bridge.currentView() === 'integrations') renderIntegrationsView();
    return {profiles: getOAuthProfiles(), accounts: getAccounts()};
  }

  async function ensureData(options = {}) {
    if (state.loaded && !options.force) {
      reconcileSelectedOAuthProfile();
      return {profiles: getOAuthProfiles(), accounts: getAccounts()};
    }
    return refreshData(options);
  }

  async function loadIntegrationsView(options = {}) {
    const {silent = false} = options;
    renderIntegrationsError('');
    try {
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      if (!silent) renderIntegrationsError(`Не удалось загрузить интеграции:\n${err.message || err}`);
    }
  }

  function syncAccountsSnapshot(accounts) {
    if (!Array.isArray(accounts)) return;
    state.accounts = accounts.slice();
    bridge.syncPublishSelections();
    if (bridge.currentView() === 'integrations') renderIntegrationsAccountsPanel();
  }

  function onIntegrationOAuthProfileChange(value) {
    state.selectedOAuthProfileId = value ? Number(value) : null;
    reconcileSelectedOAuthProfile();
    renderIntegrationsView();
  }

  function setIntegrationOAuthFormError(message) {
    if (message) {
      bridge.showInlineError('integration-oauth-form-error', message);
    } else {
      bridge.hideInlineError('integration-oauth-form-error');
    }
  }

  function setIntegrationOAuthFormMode(mode = 'json') {
    state.oauthFormMode = mode === 'edit' ? 'edit' : (mode === 'manual' ? 'manual' : 'json');
    const title = document.getElementById('integration-oauth-modal-title');
    const jsonWrap = document.getElementById('integration-oauth-json-wrap');
    const manualWrap = document.getElementById('integration-oauth-manual-wrap');
    const saveBtn = document.getElementById('integration-oauth-save-btn');
    const hint = document.querySelector('.integration-oauth-next-hint');
    if (title) {
      title.textContent = state.oauthFormMode === 'edit'
        ? 'Редактировать Google API auth'
        : state.oauthFormMode === 'manual'
          ? 'Создать Google API auth вручную'
          : 'Импортировать Google API auth из JSON';
    }
    if (jsonWrap) jsonWrap.style.display = state.oauthFormMode === 'json' ? 'block' : 'none';
    if (manualWrap) manualWrap.style.display = state.oauthFormMode === 'manual' || state.oauthFormMode === 'edit' ? 'grid' : 'none';
    if (saveBtn) {
      saveBtn.textContent = state.oauthFormMode === 'edit'
        ? 'Сохранить auth'
        : state.oauthFormMode === 'manual'
          ? 'Создать auth'
          : 'Импортировать auth';
    }
    if (hint) {
      hint.textContent = state.oauthFormMode === 'edit'
        ? 'После сохранения список Google API auth и выбор подключения канала обновятся автоматически.'
        : 'Теперь нажмите “Подключить канал”, чтобы привязать YouTube-канал к этому Google API auth.';
    }
  }

  function resetIntegrationOAuthForm(mode = 'json') {
    state.oauthEditingProfileId = null;
    setIntegrationOAuthFormMode(mode);
    setIntegrationOAuthFormError('');
    const hasProfiles = Boolean(state.oauthProfiles.length);
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
      const focusId = state.oauthFormMode === 'manual' ? 'integration-oauth-client-id' : 'integration-oauth-json';
      (document.getElementById(focusId) || document.getElementById('integration-oauth-name'))?.focus();
    }, 0);
  }

  function closeIntegrationOAuthModal(event) {
    if (event && event.target && event.target.id !== 'integration-oauth-modal') return;
    const modal = document.getElementById('integration-oauth-modal');
    if (modal) modal.style.display = 'none';
    state.oauthEditingProfileId = null;
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
      if (state.oauthFormMode === 'edit') {
        const profile = getOAuthProfileById(state.oauthEditingProfileId);
        if (!profile) throw new Error('Google API auth не найден.');
        const redirectUri = (document.getElementById('integration-oauth-redirect-uri')?.value || '').trim() || profile.redirect_uri || INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI;
        const payload = {
          name: name || profile.name || `OAuth #${profile.id}`,
          redirect_uri: redirectUri,
          status: profile.status || 'active',
        };
        if (!isEnvProfile(profile)) {
          const clientId = (document.getElementById('integration-oauth-client-id')?.value || '').trim();
          const clientSecret = (document.getElementById('integration-oauth-client-secret')?.value || '').trim();
          if (!clientId) throw new Error('Укажите client_id.');
          payload.client_id = clientId;
          if (clientSecret) payload.client_secret = clientSecret;
        }
        data = await bridge.apiPatch(`/api/publish/youtube/oauth-profiles/${Number(profile.id)}`, payload);
        const makeDefault = Boolean(document.getElementById('integration-oauth-default')?.checked);
        if (makeDefault && !profile.is_default) {
          data = await bridge.apiPost(`/api/publish/youtube/oauth-profiles/${Number(profile.id)}/set-default`, {});
        }
      } else if (state.oauthFormMode === 'manual') {
        const clientId = (document.getElementById('integration-oauth-client-id')?.value || '').trim();
        const clientSecret = (document.getElementById('integration-oauth-client-secret')?.value || '').trim();
        const redirectUri = (document.getElementById('integration-oauth-redirect-uri')?.value || '').trim() || INTEGRATION_OAUTH_DEFAULT_REDIRECT_URI;
        if (!clientId || !clientSecret) {
          throw new Error('Укажите client_id и client_secret.');
        }
        data = await bridge.apiPost('/api/publish/youtube/oauth-profiles', {
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
        data = await bridge.apiPost('/api/publish/youtube/oauth-profiles/import-client-json', {
          name,
          json_text: jsonText,
          is_default: isDefault,
        });
      }
      if (data?.profile?.id) state.selectedOAuthProfileId = Number(data.profile.id);
      closeIntegrationOAuthModal();
      bridge.showToast(state.oauthFormMode === 'edit' ? 'Google API auth обновлён' : 'Google API auth сохранён');
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      const message = err.message || 'Не удалось сохранить Google API auth';
      setIntegrationOAuthFormError(message);
      renderIntegrationsError(message);
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  function editIntegrationOAuthProfile(profileId) {
    const profile = getOAuthProfileById(profileId);
    if (!profile) return;
    renderIntegrationsError('');
    state.oauthEditingProfileId = Number(profile.id);
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
    const canEditCredentials = !isEnvProfile(profile);
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
      await bridge.apiPost(`/api/publish/youtube/oauth-profiles/${Number(profileId)}/set-default`, {});
      state.selectedOAuthProfileId = Number(profileId);
      bridge.showToast('Google API auth выбран по умолчанию');
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      renderIntegrationsError(err.message || 'Не удалось выбрать Google API auth по умолчанию');
    }
  }

  async function deleteIntegrationOAuthProfile(profileId) {
    if (!window.confirm(`Удалить Google API auth #${profileId}?`)) return;
    try {
      await bridge.apiDel(`/api/publish/youtube/oauth-profiles/${Number(profileId)}`);
      bridge.showToast('Google API auth удалён');
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      renderIntegrationsError(err.message || 'Не удалось удалить Google API auth');
    }
  }

  async function startYouTubeConnect(profileId = null) {
    await ensureData({render: false});
    const selectedId = profileId
      ? Number(profileId)
      : bridge.currentView() === 'integrations'
        ? Number(state.selectedOAuthProfileId)
        : Number(bridge.getPublishSelectedOAuthProfileId() || state.selectedOAuthProfileId);
    const selectedProfile = activeOAuthProfiles().find(profile => Number(profile.id) === selectedId) || null;
    if (!selectedProfile) {
      const message = 'OAuth-клиент не найден. Создайте OAuth профиль в настройках.';
      if (bridge.currentView() === 'integrations') {
        renderIntegrationsView();
        renderIntegrationsError(message);
      } else {
        bridge.renderPublishError(message);
      }
      bridge.showToast(message, 'err');
      return;
    }
    const popup = window.open('about:blank', 'shortsfarm_youtube_oauth');
    if (popup) {
      try {
        popup.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>ShortsFarm · YouTube OAuth</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f4f5;color:#18181b;font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{padding:24px 28px;border:1px solid #d4d4d8;border-radius:14px;background:#fff;box-shadow:0 20px 40px rgba(0,0,0,.08)}h1{margin:0 0 8px;font-size:20px}p{margin:0;color:#52525b}</style></head><body><main><h1>Открываю Google OAuth…</h1><p>Подождите пару секунд.</p></main></body></html>`);
        popup.document.close();
      } catch {}
    }
    bridge.renderPublishError('');
    renderIntegrationsError('');
    state.connectBusy = true;
    renderIntegrationsConnectButton();
    bridge.renderPublishConnectButton();
    try {
      const data = await bridge.apiPost('/api/publish/youtube/connect/start', {oauth_profile_id: Number(selectedProfile.id)});
      if (!data?.auth_url) throw new Error('Google OAuth URL не получен');
      if (popup && !popup.closed) {
        popup.location.href = data.auth_url;
        popup.focus?.();
        bridge.showToast('Открываю Google Consent Screen');
      } else {
        bridge.showToast('Браузер заблокировал новую вкладку. Открываю авторизацию в текущем окне.', 'warn');
        window.location.href = data.auth_url;
      }
    } catch (err) {
      if (popup && !popup.closed) {
        popup.close();
      }
      const message = err.message || 'Не удалось начать подключение YouTube';
      if (bridge.currentView() === 'integrations') renderIntegrationsError(message);
      else bridge.renderPublishError(message);
      bridge.showToast(message, 'err');
    } finally {
      state.connectBusy = false;
      renderIntegrationsConnectButton();
      bridge.renderPublishConnectButton();
    }
  }

  async function disconnectYouTubeAccount(accountId) {
    if (!accountId) return;
    bridge.renderPublishError('');
    renderIntegrationsError('');
    try {
      await bridge.apiPost(`/api/publish/youtube/accounts/${accountId}/disconnect`, {});
      bridge.showToast('YouTube канал отключён');
      await refreshData({render: false});
      if (bridge.currentView() === 'integrations') renderIntegrationsView();
      else if (bridge.currentView() === 'storage-profile') await bridge.reloadStorageProfile();
      else await bridge.refreshPublishView({silent: true});
    } catch (err) {
      const message = `Не удалось отключить канал:\n${err.message || err}`;
      if (bridge.currentView() === 'integrations') renderIntegrationsError(message);
      else bridge.renderPublishError(message);
    }
  }

  async function syncYouTubeAccountMetadata(accountId) {
    if (!accountId) return;
    renderIntegrationsError('');
    try {
      const data = await bridge.apiPost(`/api/publish/youtube/accounts/${Number(accountId)}/sync-metadata`, {});
      if (data.status === 'failed') {
        renderIntegrationsError(data.error || 'Не удалось обновить данные канала');
        bridge.showToast('Данные канала не обновлены', 'err');
      } else {
        bridge.showToast('Данные YouTube-канала обновлены');
      }
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      const message = err.message || 'Не удалось обновить данные канала';
      renderIntegrationsError(message);
      bridge.showToast(message, 'err');
    }
  }

  async function syncAllYouTubeAccountsMetadata() {
    renderIntegrationsError('');
    try {
      const data = await bridge.apiPost('/api/publish/youtube/accounts/sync-metadata', {});
      const summary = data.summary || {};
      const failed = Number(summary.failed || 0);
      bridge.showToast(`Обновлено каналов: ${summary.ok || 0} · ошибок: ${failed}`, failed ? 'err' : 'ok');
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      const message = err.message || 'Не удалось обновить данные каналов';
      renderIntegrationsError(message);
      bridge.showToast(message, 'err');
    }
  }

  async function editYouTubeAccountAlias(accountId) {
    const account = state.accounts.find(item => Number(item.id) === Number(accountId));
    if (!account) return;
    const alias = await bridge.openTextActionModal({
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
      await bridge.apiPatch(`/api/publish/youtube/accounts/${Number(accountId)}`, {local_alias: alias});
      bridge.showToast('Локальный alias сохранён');
      await refreshData({render: false});
      renderIntegrationsView();
    } catch (err) {
      const message = err.message || 'Не удалось сохранить alias';
      renderIntegrationsError(message);
      bridge.showToast(message, 'err');
    }
  }

  function handleOAuthEvent(payload) {
    const ok = Boolean(payload?.ok);
    const message = payload?.message || '';
    if (ok) {
      bridge.showToast('YouTube канал подключён. Обновляю список каналов...');
    } else {
      bridge.showToast(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.', 'err');
      if (bridge.currentView() === 'integrations') {
        renderIntegrationsError(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.');
      } else if (bridge.currentView() === 'publish') {
        bridge.renderPublishError(message || 'Подключение YouTube не завершено. Попробуйте ещё раз.');
      }
    }
    bridge.loadSettingsView({silent: true});
    refreshData({render: false})
      .then(() => {
        if (bridge.currentView() === 'integrations') renderIntegrationsView();
        else bridge.refreshPublishView({silent: true});
        if (bridge.currentView() === 'storage-profile') bridge.reloadStorageProfile();
      })
      .catch(err => {
        const errorMessage = err.message || 'Не удалось обновить данные YouTube';
        if (bridge.currentView() === 'integrations') renderIntegrationsError(errorMessage);
        else bridge.renderPublishError(errorMessage);
      });
  }

  function isConnectBusy() {
    return Boolean(state.connectBusy);
  }

  window.ShortsFarmIntegrations = {
    configure,
    ensureData,
    getAccounts,
    getActiveOAuthProfiles,
    getOAuthProfileById,
    getOAuthProfiles,
    handleOAuthEvent,
    isConnectBusy,
    isEnvProfile,
    loadIntegrationsView,
    openStorageProfile: profileId => bridge.openStorageProfile(profileId),
    profileSourceLabel,
    refreshData,
    startYouTubeConnect,
    syncAccountsSnapshot,
  };

  Object.assign(window, {
    closeIntegrationOAuthModal,
    createIntegrationOAuthProfile,
    deleteIntegrationOAuthProfile,
    disconnectYouTubeAccount,
    editIntegrationOAuthProfile,
    editYouTubeAccountAlias,
    loadIntegrationsView,
    onIntegrationOAuthProfileChange,
    renderIntegrationsAccountsPanel,
    saveIntegrationOAuthProfile,
    setIntegrationDefaultOAuthProfile,
    setIntegrationOAuthFormMode,
    startYouTubeConnect,
    syncAllYouTubeAccountsMetadata,
    syncYouTubeAccountMetadata,
  });
})();
