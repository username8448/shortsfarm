"""Frontend contracts for the legacy Storage Profiles view split."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from shortsfarm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "shortsfarm" / "web" / "templates" / "index.html"
STORAGE_PARTIAL = ROOT / "shortsfarm" / "web" / "templates" / "views" / "storage_profiles.html"
APP_JS = ROOT / "shortsfarm" / "web" / "static" / "app.js"
WEB_APP = ROOT / "shortsfarm" / "web" / "app.py"
STORAGE_JS = ROOT / "shortsfarm" / "web" / "static" / "js" / "features" / "storage-profiles.js"

STORAGE_IDS = [
    "v-storage-profiles",
    "storage-profiles-error",
    "storage-profiles-grid",
    "v-storage-profile",
    "storage-profile-view-title",
    "storage-profile-view-subtitle",
    "storage-profile-error",
    "storage-profile-page-head-title",
    "storage-profile-detail",
]

PUBLIC_CORE_HANDLERS = [
    "loadStorageProfiles",
    "createStorageProfile",
    "selectStorageProfile",
    "openStorageProfile",
    "openStorageProfilesHub",
    "loadStorageProfileDetail",
    "saveStorageProfile",
    "disableStorageProfile",
    "setStorageProfileTab",
    "openStorageProfileDrawer",
    "closeStorageProfileDrawer",
    "toggleStorageProfileItemSelection",
    "setAllStorageProfileItemSelection",
    "saveStorageProfileTagRules",
    "addStorageProfileTagRule",
    "removeStorageProfileTagRule",
    "runStorageProfileTagSync",
    "openStorageProfileVideoPicker",
    "toggleStorageCandidatePicker",
    "onStorageCatalogSearchInput",
    "loadRandomStorageCatalogVideos",
    "toggleStorageCandidateSelection",
    "addCandidateToStorageProfile",
    "addSelectedCatalogVideosToStorageProfile",
    "removeStorageProfileItem",
    "addWorkspacePathToStorageProfile",
    "addWorkspaceItemToStorageProfile",
]

CORE_STATE_DECLARATIONS = [
    "let storageProfiles",
    "let currentStorageProfileId",
    "let currentStorageProfile",
    "let storageProfileItems",
    "let storageProfileActiveTab",
    "let storageProfileDrawerOpen",
    "let storageProfileDrawerSection",
    "let selectedStorageProfileItemIds",
    "let storageProfileCandidates",
    "let selectedStorageCandidatePaths",
    "let storageCatalogSearchQuery",
    "let storageCatalogSearchTimer",
    "let storageCandidatePickerOpen",
]

ADVANCED_APP_FUNCTIONS = [
    "linkStorageProfileYoutube",
    "unlinkStorageProfileYoutube",
    "syncStorageProfileYoutubeBranding",
    "toggleStorageProfileYoutubeBranding",
    "setStorageProfileBrandingOverride",
    "syncStorageProfileYoutube",
    "storageProfilePublishSettingsPanel",
    "saveStorageProfilePublishSettings",
    "storageProfileChannelSettingsPanel",
    "saveStorageProfileChannelSettings",
    "storageProfilePublishJobsPanel",
    "storageProfileYoutubeVideosPanel",
    "refreshStorageProfilePublishState",
    "enqueueStorageProfileSelection",
]


def test_storage_profiles_views_are_jinja_partial() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    partial = STORAGE_PARTIAL.read_text(encoding="utf-8")

    assert index_html.count('{% include "views/storage_profiles.html" %}') == 1
    assert 'id="v-storage-profiles"' not in index_html
    assert 'id="v-storage-profile"' not in index_html

    for element_id in STORAGE_IDS:
        assert partial.count(f'id="{element_id}"') == 1

    assert partial.index('id="v-storage-profiles"') < partial.index('id="v-storage-profile"')


def test_rendered_index_contains_storage_profiles_partial_once_and_script_order() -> None:
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    html = response.text

    for element_id in STORAGE_IDS:
        assert html.count(f'id="{element_id}"') == 1

    files_script = "/static/js/features/files.js?v="
    tags_script = "/static/js/features/tags.js?v="
    storage_script = "/static/js/features/storage-profiles.js?v="
    app_script = "/static/app.js?v="
    assert files_script in html
    assert tags_script in html
    assert storage_script in html
    assert app_script in html
    assert html.index(files_script) < html.index(tags_script) < html.index(storage_script) < html.index(app_script)


def test_storage_profiles_feature_boundary_and_exports() -> None:
    storage_js = STORAGE_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert storage_js.lstrip().startswith("(() => {")
    assert "window.ShortsFarmStorageProfiles" in storage_js
    assert "configure(callbacks = {})" in storage_js
    assert "loadAdvancedProfileData: async () => ({})" in storage_js
    assert "fallbackCatalogTags" not in storage_js
    assert "cachedCatalogTags" not in storage_js
    assert "catalogTags" not in storage_js
    assert "/api/tags" not in storage_js
    assert "DOMContentLoaded" not in storage_js
    assert "setInterval" not in storage_js
    assert re.search(r"^\s*(import|export)\s", storage_js, re.MULTILINE) is None
    assert re.search(r"\bconst\s+api\s*=", storage_js) is None
    assert re.search(r"\blet\s+api\s*=", storage_js) is None

    public_api = storage_js.split("const publicApi = {", 1)[1].split("};", 1)[0]
    assert "state" not in public_api
    for handler in PUBLIC_CORE_HANDLERS:
        assert handler in storage_js
        assert re.search(rf"\bfunction\s+{re.escape(handler)}\s*\(", app_js) is None
        assert re.search(rf"\basync\s+function\s+{re.escape(handler)}\s*\(", app_js) is None


def test_storage_profiles_app_bridge_keeps_advanced_ownership() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_py = WEB_APP.read_text(encoding="utf-8")

    assert '"static" / "js" / "features" / "storage-profiles.js"' in app_py
    assert "window.ShortsFarmStorageProfiles?.configure?.({" in app_js
    assert "loadAdvancedProfileData: async profileId =>" in app_js
    assert "loadStorageYoutubeAccounts().catch" in app_js
    assert "loadEditingSupportData().catch" in app_js
    assert "pickStorageProfile: profiles => openStorageProfilePickModal(profiles)" in app_js
    assert "renderProfilePublishControls: () => storageProfilePublishControls()" in app_js
    assert "renderProfileChannelSettingsPanel: profile => storageProfileChannelSettingsPanel(profile)" in app_js
    assert "workspaceButtonHtml" in app_js

    for declaration in CORE_STATE_DECLARATIONS:
        assert declaration not in app_js

    for function_name in ADVANCED_APP_FUNCTIONS:
        assert f"function {function_name}" in app_js or f"async function {function_name}" in app_js


def test_storage_profiles_routing_candidate_sync_and_oauth_boundaries() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    storage_js = STORAGE_JS.read_text(encoding="utf-8")

    nav_body = app_js.split("function nav(id, btn) {", 1)[1].split("function activateInitialViewFromQuery", 1)[0]
    assert "window.ShortsFarmStorageProfiles?.openStorageProfilesHub?.({replace: true})" in nav_body
    assert "currentStorageProfile" not in nav_body

    assert "window.ShortsFarmStorageProfiles?.openStorageProfile?.(profileId, {replace: true})" in app_js
    assert "window.addEventListener('popstate'" in app_js
    assert app_js.count("window.addEventListener('popstate'") == 1
    assert "window.ShortsFarmStorageProfiles?.handleRouteFromLocation?.();" in app_js
    assert "handleRouteFromLocation" in storage_js
    assert "searchParams.set('profile'" in storage_js

    update_body = app_js.split("function updateWorkspaceItemCatalogTags", 1)[1].split("async function loadStorageYoutubeAccounts", 1)[0]
    assert "window.ShortsFarmStorageProfiles?.syncCatalogVideoTags?.(workspacePath, tags || [], updatedItem)" in update_body
    assert "storageProfileCandidates" not in update_body

    sync_body = storage_js.split("function syncCatalogVideoTags", 1)[1].split("function applyProfileUpdate", 1)[0]
    assert "state.candidates = state.candidates.map" in sync_body
    assert "bridge.api" not in sync_body
    assert "renderStorageProfileDetail" not in sync_body

    oauth_body = app_js.split("function handleOAuthEvent", 1)[1].split("window.addEventListener('DOMContentLoaded'", 1)[0]
    assert "window.ShortsFarmStorageProfiles?.reloadCurrentProfile?.();" in oauth_body
    assert "currentStorageProfileId" not in oauth_body
