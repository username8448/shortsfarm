"""Frontend contracts for the legacy Integrations view split."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from shortsfarm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "shortsfarm" / "web" / "templates" / "index.html"
INTEGRATIONS_PARTIAL = ROOT / "shortsfarm" / "web" / "templates" / "views" / "integrations.html"
INTEGRATION_OAUTH_MODAL = ROOT / "shortsfarm" / "web" / "templates" / "components" / "integration_oauth_modal.html"
APP_JS = ROOT / "shortsfarm" / "web" / "static" / "app.js"
WEB_APP = ROOT / "shortsfarm" / "web" / "app.py"
INTEGRATIONS_JS = ROOT / "shortsfarm" / "web" / "static" / "js" / "features" / "integrations.js"

INTEGRATIONS_IDS = [
    "v-integrations",
    "integrations-error",
    "integrations-oauth-profiles",
    "integrations-profile-select",
    "integrations-connect-state",
    "integrations-search",
    "integrations-accounts-list",
]

INTEGRATION_MODAL_IDS = [
    "integration-oauth-modal",
    "integration-oauth-modal-title",
    "integration-oauth-form-error",
    "integration-oauth-name",
    "integration-oauth-json",
    "integration-oauth-client-id",
    "integration-oauth-client-secret",
    "integration-oauth-redirect-uri",
    "integration-oauth-default",
    "integration-oauth-save-btn",
]

MOVED_INTEGRATION_FUNCTIONS = [
    "renderIntegrationsError",
    "integrationLinkedProfiles",
    "integrationAccountSearchText",
    "formatChannelNumber",
    "youtubeAccountAvatarHtml",
    "youtubeAccountStatsHtml",
    "renderIntegrationsOAuthSelect",
    "renderIntegrationsConnectState",
    "renderIntegrationsOAuthProfilesPanel",
    "renderIntegrationsAccountsPanel",
    "renderIntegrationsView",
    "loadIntegrationsView",
    "onIntegrationOAuthProfileChange",
    "setIntegrationOAuthFormError",
    "setIntegrationOAuthFormMode",
    "resetIntegrationOAuthForm",
    "createIntegrationOAuthProfile",
    "closeIntegrationOAuthModal",
    "saveIntegrationOAuthProfile",
    "editIntegrationOAuthProfile",
    "setIntegrationDefaultOAuthProfile",
    "deleteIntegrationOAuthProfile",
    "startYouTubeConnect",
    "disconnectYouTubeAccount",
    "syncYouTubeAccountMetadata",
    "syncAllYouTubeAccountsMetadata",
    "editYouTubeAccountAlias",
]

REMOVED_APP_STATE = [
    "let lastYoutubeAccounts",
    "let lastYoutubeProfiles",
    "let integrationsSearchQuery",
    "let integrationOAuthFormMode",
    "let integrationOAuthEditingProfileId",
]


def test_integrations_view_and_modal_are_jinja_partials() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    partial = INTEGRATIONS_PARTIAL.read_text(encoding="utf-8")
    modal = INTEGRATION_OAUTH_MODAL.read_text(encoding="utf-8")

    assert index_html.count('{% include "views/integrations.html" %}') == 1
    assert index_html.count('{% include "components/integration_oauth_modal.html" %}') == 1
    assert 'id="v-integrations"' not in index_html
    assert 'id="integration-oauth-modal"' not in index_html

    for element_id in INTEGRATIONS_IDS:
        assert partial.count(f'id="{element_id}"') == 1
    for element_id in INTEGRATION_MODAL_IDS:
        assert modal.count(f'id="{element_id}"') == 1

    assert "Google API auth" in partial
    assert "YouTube-аккаунты" in partial
    assert "OAuth Client JSON" in modal


def test_rendered_index_contains_integrations_once_and_script_order() -> None:
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    html = response.text

    for element_id in INTEGRATIONS_IDS + INTEGRATION_MODAL_IDS:
        assert html.count(f'id="{element_id}"') == 1

    files_script = "/static/js/features/files.js?v="
    tags_script = "/static/js/features/tags.js?v="
    storage_script = "/static/js/features/storage-profiles.js?v="
    integrations_script = "/static/js/features/integrations.js?v="
    app_script = "/static/app.js?v="
    assert files_script in html
    assert tags_script in html
    assert storage_script in html
    assert integrations_script in html
    assert app_script in html
    assert html.index(files_script) < html.index(tags_script) < html.index(storage_script) < html.index(integrations_script) < html.index(app_script)


def test_integrations_asset_version_and_feature_boundary() -> None:
    app_py = WEB_APP.read_text(encoding="utf-8")
    integrations_js = INTEGRATIONS_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '"static" / "js" / "features" / "integrations.js"' in app_py
    assert integrations_js.lstrip().startswith("(function () {")
    assert "window.ShortsFarmIntegrations = {" in integrations_js
    assert "configure(options = {})" in integrations_js
    assert "DOMContentLoaded" not in integrations_js
    assert "addEventListener" not in integrations_js
    assert "setInterval" not in integrations_js
    assert re.search(r"^\s*(import|export)\s", integrations_js, re.MULTILINE) is None
    assert re.search(r"\bconst\s+api\s*=", integrations_js) is None
    assert re.search(r"\blet\s+api\s*=", integrations_js) is None

    for state_name in REMOVED_APP_STATE:
        assert state_name not in app_js


def test_integrations_functions_moved_out_of_app_js() -> None:
    integrations_js = INTEGRATIONS_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    for function_name in MOVED_INTEGRATION_FUNCTIONS:
        assert f"function {function_name}" in integrations_js or f"async function {function_name}" in integrations_js
        assert re.search(rf"\bfunction\s+{re.escape(function_name)}\s*\(", app_js) is None
        assert re.search(rf"\basync\s+function\s+{re.escape(function_name)}\s*\(", app_js) is None

    assert "publishState.selectedProfileId" in app_js
    assert "publishState.selectedAccountId" in app_js
    assert "window.ShortsFarmIntegrations?.ensureData?.({render: false})" in app_js
    assert "window.ShortsFarmIntegrations?.getAccounts?.()" in app_js
    assert "window.ShortsFarmIntegrations?.getActiveOAuthProfiles?.()" in app_js
    assert "window.ShortsFarmIntegrations?.handleOAuthEvent?.(payload)" in app_js
    assert "api.get('/api/publish/youtube/oauth-profiles')" not in app_js
    assert "api.get('/api/publish/youtube/accounts')" not in app_js


def test_integrations_namespace_is_narrow_and_state_private() -> None:
    integrations_js = INTEGRATIONS_JS.read_text(encoding="utf-8")

    public_api = integrations_js.split("window.ShortsFarmIntegrations = {", 1)[1].split("};", 1)[0]
    for public_name in [
        "configure",
        "loadIntegrationsView",
        "ensureData",
        "refreshData",
        "getOAuthProfiles",
        "getActiveOAuthProfiles",
        "getAccounts",
        "getOAuthProfileById",
        "profileSourceLabel",
        "isEnvProfile",
        "isConnectBusy",
        "startYouTubeConnect",
        "handleOAuthEvent",
        "syncAccountsSnapshot",
    ]:
        assert public_name in public_api

    assert "state" not in public_api
    assert "getState" not in public_api
    assert "setState" not in public_api
    assert "return copyItems(state.oauthProfiles)" in integrations_js
    assert "return copyItems(state.accounts)" in integrations_js


def test_integrations_app_bridge_and_cross_domain_boundaries() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    integrations_js = INTEGRATIONS_JS.read_text(encoding="utf-8")

    assert "window.ShortsFarmIntegrations?.configure?.({" in app_js
    assert "apiGet: path => api.get(path)" in app_js
    assert "getPublishSelectedOAuthProfileId: () => publishState.selectedProfileId" in app_js
    assert "renderPublishConnectButton," in app_js
    assert "renderPublishError," in app_js
    assert "openTextActionModal," in app_js
    assert "window.ShortsFarmIntegrations?.syncAccountsSnapshot?.(accounts)" in app_js
    assert "window.ShortsFarmIntegrations?.loadIntegrationsView?.({silent: true})" in app_js
    assert "window.ShortsFarmIntegrations?.isConnectBusy?.()" in app_js
    assert "ShortsFarmIntegrations.openStorageProfile" in integrations_js

    app_exports = app_js.split("Object.assign(window,", 1)[1].split("});", 1)[0]
    assert "loadIntegrationsView" not in app_exports
    assert "createIntegrationOAuthProfile" not in app_exports
    assert "disconnectYouTubeAccount" not in app_exports

    integration_exports = integrations_js.split("Object.assign(window,", 1)[1].split("});", 1)[0]
    assert "loadIntegrationsView" in integration_exports
    assert "createIntegrationOAuthProfile" in integration_exports
    assert "disconnectYouTubeAccount" in integration_exports
